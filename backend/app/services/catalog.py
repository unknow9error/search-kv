import asyncio
import hashlib
import logging
import time
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

import httpx
from pydantic import ValidationError
from sqlalchemy import case, delete, exists, or_, select, update

from app.core.config import Settings
from app.core.coordination import BusyError, Coordination
from app.core.db import Database
from app.core.telemetry import PROVIDER_CALLS, PROVIDER_LATENCY
from app.domain.models import Amenity, Apartment, ImportRun, Place, ProjectFact, Provider, aware, utcnow
from app.domain.schemas import AmenityOut, Listing, Preferences, ProjectFactOut, Snapshot, distance_m
from app.providers.base import ApartmentProvider, SourceError
from app.services.places import near_places

log = logging.getLogger("meken")
AMENITY_NAMES = {
    "school": "Школа",
    "kindergarten": "Детский сад",
    "park": "Парк",
    "transit": "Остановка",
}


def listing_id(provider_id: str, external_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"meken:{provider_id}:{external_id}"))


class Catalog:
    def __init__(
        self,
        db: Database,
        coordination: Coordination,
        providers: list[ApartmentProvider],
        settings: Settings,
    ):
        self.db, self.coordination, self.settings = db, coordination, settings
        self.providers = {x.id: x for x in providers}
        self.semaphore = asyncio.Semaphore(settings.source_concurrency)

    async def ingest(self, provider_id: str, snapshot: Snapshot) -> int:
        """Atomic inventory batch. Missing != sold; old observations cannot overwrite newer ones."""
        if len(snapshot.items) > self.settings.max_snapshot_items:
            raise SourceError("catalog_limit")
        if len(snapshot.projects) > self.settings.max_snapshot_items:
            raise SourceError("catalog_limit")
        if any(c.provider_id != provider_id for c in snapshot.project_contexts):
            raise SourceError("context_provider_mismatch")
        now = utcnow()
        source = self.providers.get(provider_id)
        public_source = source is not None and (
            getattr(source, "public_data", False)
            or (self.settings.env == "demo" and getattr(source, "demo", False))
        )
        if snapshot.projects and not public_source:
            raise SourceError("project_source_not_public")
        async with self.db.sessions.begin() as session:
            provider = await session.scalar(
                select(Provider).where(Provider.id == provider_id).with_for_update()
            )
            if provider is None:
                raise SourceError("unknown_provider")
            from app.services.projects import (
                project_projection_from_lots,
                upsert_public_projects,
            )

            if public_source:
                await upsert_public_projects(session, provider_id, snapshot.projects, snapshot.as_of)
            existing = {
                r.external_id: r
                for r in (
                    await session.scalars(
                        select(Apartment).where(
                            Apartment.provider_id == provider_id,
                            Apartment.external_id.in_([x.external_id for x in snapshot.items]),
                        )
                    )
                ).all()
            }
            places = (
                await session.scalars(
                    select(Place).where(
                        Place.city.in_({x.city for x in snapshot.items}),
                        Place.observed_at >= now - timedelta(days=180),
                        Place.state == "operating",
                    )
                )
            ).all()
            places_by_city = {}
            for place in places:
                places_by_city.setdefault(place.city, []).append(place)
            accepted = 0
            updated_ids, replacement_amenities = [], []
            for item in snapshot.items:
                row = existing.get(item.external_id)
                if row and aware(row.observed_at) > snapshot.as_of:
                    continue
                values = item.model_dump(mode="json", exclude={"amenities"})
                values.update(observed_at=snapshot.as_of, received_at=now)
                if row:
                    for key, value in values.items():
                        setattr(row, key, value)
                    row.version += 1
                else:
                    row = Apartment(
                        id=listing_id(provider_id, item.external_id), provider_id=provider_id, **values
                    )
                    session.add(row)
                updated_ids.append(row.id)
                for poi in item.amenities:
                    replacement_amenities.append(
                        Amenity(
                            apartment_id=row.id,
                            kind=poi.kind,
                            state=poi.state,
                            name=poi.name,
                            latitude=poi.latitude,
                            longitude=poi.longitude,
                            distance_m=distance_m(
                                item.latitude, item.longitude, poi.latitude, poi.longitude
                            ),
                            source_url=str(poi.source_url),
                            observed_at=poi.observed_at,
                        )
                    )
                for place, distance in near_places(
                    item.latitude, item.longitude, places_by_city.get(item.city, [])
                ):
                    replacement_amenities.append(
                        Amenity(
                            apartment_id=row.id,
                            kind=place.kind,
                            state=place.state,
                            name=place.name,
                            latitude=place.latitude,
                            longitude=place.longitude,
                            distance_m=distance,
                            source_url=place.source_url,
                            observed_at=place.observed_at,
                        )
                    )
                accepted += 1
            await session.flush()
            if public_source:
                await project_projection_from_lots(session, provider_id, snapshot.items, snapshot.as_of)
            if updated_ids:
                await session.execute(delete(Amenity).where(Amenity.apartment_id.in_(updated_ids)))
            session.add_all(replacement_amenities)
            if snapshot.complete:
                statement = update(Apartment).where(
                    Apartment.provider_id == provider_id,
                    Apartment.observed_at <= snapshot.as_of,
                    Apartment.external_id.not_in([x.external_id for x in snapshot.items]),
                )
                if snapshot.scope_city:
                    statement = statement.where(Apartment.city == snapshot.scope_city)
                await session.execute(
                    statement.execution_options(synchronize_session=False).values(
                        status="unknown",
                        observed_at=snapshot.as_of,
                        received_at=now,
                        version=Apartment.version + 1,
                    )
                )
            if not provider.last_snapshot_at or aware(provider.last_snapshot_at) < snapshot.as_of:
                provider.last_snapshot_at = snapshot.as_of
            session.add(ImportRun(provider_id=provider_id, outcome="success", count=accepted))
        from app.services.project_context import ingest_context

        for context in snapshot.project_contexts:
            await ingest_context(self.db, context)
        return accepted

    async def refresh_provider(
        self,
        provider: ApartmentProvider,
        city: str | None = None,
        external_id: str | None = None,
        preferences: Preferences | None = None,
        on_batch=None,
    ) -> dict:
        key = "provider:" + provider.id
        started = time.monotonic()
        result = {"provider_id": provider.id, "name": provider.name, "status": "error", "count": 0}
        try:
            if await self.coordination.get(key + ":circuit"):
                result["status"] = "temporarily_unavailable"
                return result
            async with self.semaphore, self.coordination.lease(key, 90):
                fingerprint = (
                    hashlib.sha256(preferences.model_dump_json().encode()).hexdigest()[:20]
                    if preferences
                    else (city or "all")
                )
                throttle_key = key + ":last:" + (external_id or fingerprint)
                if await self.coordination.get(throttle_key):
                    result["status"] = "recently_checked"
                    return result
                if external_id or not hasattr(provider, "fetch_pages"):
                    async with asyncio.timeout(self.settings.source_timeout_seconds):
                        snapshot = (
                            await provider.verify(external_id)
                            if external_id
                            else await provider.fetch(city)
                        )
                    async with asyncio.timeout(30):
                        result["count"] = await self.ingest(provider.id, snapshot)
                else:
                    async with asyncio.timeout(self.settings.source_timeout_seconds + 20):
                        async for snapshot in provider.fetch_pages(city, preferences):
                            count = await self.ingest(provider.id, snapshot)
                            result["count"] += count
                            if on_batch:
                                await on_batch(
                                    {
                                        "provider_id": provider.id,
                                        "name": provider.name,
                                        "status": "batch",
                                        "count": count,
                                    }
                                )
                await self.coordination.set(throttle_key, "1", 15 if external_id else 30)
                await self.coordination.set(key + ":failures", "0", 120)
                result["status"] = "complete"
        except BusyError:
            result["status"] = "updating"
        except (TimeoutError, httpx.TimeoutException):
            result["status"] = "timeout"
        except (httpx.HTTPError, SourceError, ValidationError, ValueError) as exc:
            result["status"] = exc.code if isinstance(exc, SourceError) else "invalid_response"
        except Exception:
            log.error("provider_refresh_internal_error")
            result["status"] = "unavailable"
        finally:
            PROVIDER_LATENCY.labels(provider.id).observe(time.monotonic() - started)
            PROVIDER_CALLS.labels(provider.id, result["status"]).inc()
        if result["status"] not in {
            "complete",
            "recently_checked",
            "updating",
            "temporarily_unavailable",
        }:
            failures = await self.coordination.increment(key + ":failures", 120)
            if failures >= 3:
                await self.coordination.set(key + ":circuit", "1", 60)
            async with self.db.sessions.begin() as session:
                session.add(
                    ImportRun(provider_id=provider.id, outcome="failed", error_code=result["status"])
                )
        return result

    async def refresh_events(self, city: str | None, preferences: Preferences | None = None):
        providers = [p for p in self.providers.values() if city is None or city in p.cities]
        queue = asyncio.Queue(maxsize=32)

        async def run(provider):
            try:
                result = await self.refresh_provider(
                    provider, city, preferences=preferences, on_batch=queue.put
                )
                await queue.put(result)
            except Exception:
                await queue.put(
                    {
                        "provider_id": provider.id,
                        "name": provider.name,
                        "status": "unavailable",
                        "count": 0,
                    }
                )
            finally:
                if not asyncio.current_task().cancelling():
                    await queue.put(None)

        tasks = [asyncio.create_task(run(p)) for p in providers]
        remaining = len(tasks)
        try:
            while remaining:
                item = await queue.get()
                if item is None:
                    remaining -= 1
                else:
                    yield item
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            # Drain queued notifications so cancellation cannot block on a full queue.
            while not queue.empty():
                queue.get_nowait()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _amenity_exists(self, kind: str, prefs: Preferences):
        if prefs.amenity_scope in {"complex", "bigville"}:
            scope_id = (
                Apartment.complex_id if prefs.amenity_scope == "complex" else Apartment.bigville_id
            )
            return exists(
                select(ProjectFact.id).where(
                    ProjectFact.provider_id == Apartment.provider_id,
                    ProjectFact.scope_type == prefs.amenity_scope,
                    ProjectFact.scope_id == scope_id,
                    ProjectFact.kind == kind,
                    ProjectFact.state == "operating",
                    ProjectFact.relation == "within",
                    ProjectFact.expires_at > utcnow(),
                )
            )
        return exists(
            select(Amenity.id).where(
                Amenity.apartment_id == Apartment.id,
                Amenity.kind == kind,
                Amenity.state == "operating",
                Amenity.distance_m <= prefs.amenity_radius_m,
                Amenity.observed_at >= utcnow() - timedelta(days=180),
            )
        )

    async def search(self, prefs: Preferences, limit=20) -> list[Listing]:
        statement = (
            select(Apartment, Provider)
            .join(Provider)
            .where(
                Provider.enabled.is_(True),
                Apartment.status == "available",
                Apartment.observed_at
                >= utcnow() - timedelta(seconds=self.settings.max_catalog_age_seconds),
            )
        )
        if self.settings.env != "demo":
            statement = statement.where(Provider.demo.is_(False))
        if prefs.city:
            statement = statement.where(Apartment.city == prefs.city)
        if prefs.budget_max:
            statement = statement.where(Apartment.price_kzt <= prefs.budget_max)
        if prefs.rooms:
            statement = statement.where(Apartment.rooms.in_(prefs.rooms))
        if prefs.area_min:
            statement = statement.where(Apartment.area_m2 >= prefs.area_min)
        if prefs.floor_min:
            statement = statement.where(Apartment.floor >= prefs.floor_min)
        if prefs.floor_max:
            statement = statement.where(Apartment.floor <= prefs.floor_max)
        for kind in prefs.required_amenities:
            statement = statement.where(self._amenity_exists(kind, prefs))
        score = case(
            (Apartment.observed_at >= utcnow() - timedelta(seconds=self.settings.fresh_seconds), 2),
            else_=0,
        )
        for kind in set(prefs.preferred_amenities):
            score = score + case((self._amenity_exists(kind, prefs), 10), else_=0)
        statement = statement.order_by(score.desc(), Apartment.price_kzt, Apartment.id).limit(limit)
        async with self.db.sessions() as session:
            rows = (await session.execute(statement)).all()
            amenities = (
                (
                    await session.scalars(
                        select(Amenity).where(Amenity.apartment_id.in_([a.id for a, _ in rows]))
                    )
                ).all()
                if rows
                else []
            )
            facts = await self.project_facts(session, [a for a, _ in rows])
        by_apartment: dict[str, list[Amenity]] = {}
        for amenity in amenities:
            by_apartment.setdefault(amenity.apartment_id, []).append(amenity)
        return [self.present(a, p, by_apartment.get(a.id, []), prefs, facts) for a, p in rows]

    async def get(self, apartment_id: str, prefs: Preferences | None = None) -> Listing | None:
        async with self.db.sessions() as session:
            row = (
                await session.execute(
                    select(Apartment, Provider)
                    .join(Provider)
                    .where(Apartment.id == apartment_id, Provider.enabled.is_(True))
                )
            ).first()
            if not row or (row[1].demo and self.settings.env != "demo"):
                return None
            amenities = list(
                (
                    await session.scalars(select(Amenity).where(Amenity.apartment_id == apartment_id))
                ).all()
            )
            facts = await self.project_facts(session, [row[0]])
        return self.present(*row, amenities, prefs or Preferences(), facts)

    async def project_facts(self, session, apartments):
        if not apartments:
            return []
        return list(
            (
                await session.scalars(
                    select(ProjectFact)
                    .where(
                        ProjectFact.provider_id.in_({a.provider_id for a in apartments}),
                        ProjectFact.expires_at > utcnow(),
                        or_(
                            (ProjectFact.scope_type == "complex")
                            & ProjectFact.scope_id.in_(
                                {a.complex_id for a in apartments if a.complex_id}
                            ),
                            (ProjectFact.scope_type == "bigville")
                            & ProjectFact.scope_id.in_(
                                {a.bigville_id for a in apartments if a.bigville_id}
                            ),
                        ),
                    )
                    .order_by(ProjectFact.observed_at.desc())
                )
            ).all()
        )

    def present(
        self,
        apartment: Apartment,
        provider: Provider,
        amenities: list[Amenity],
        prefs: Preferences,
        facts: list[ProjectFact] | None = None,
    ) -> Listing:
        scoped_facts = [
            f
            for f in (facts or [])
            if f.provider_id == apartment.provider_id
            and (
                (f.scope_type == "complex" and f.scope_id == apartment.complex_id)
                or (f.scope_type == "bigville" and f.scope_id == apartment.bigville_id)
            )
        ][:12]
        reasons, tradeoffs = [], []
        fresh = (utcnow() - aware(apartment.observed_at)).total_seconds() <= self.settings.fresh_seconds
        if prefs.budget_max:
            if apartment.price_kzt <= prefs.budget_max:
                reasons.append("В пределах вашего бюджета")
            else:
                tradeoffs.append("Цена выше вашего бюджета")
        if prefs.rooms and apartment.rooms in prefs.rooms:
            reasons.append("Подходит по количеству комнат")
        for kind in dict.fromkeys(prefs.required_amenities + prefs.preferred_amenities):
            if prefs.amenity_scope in {"complex", "bigville"}:
                matching = [
                    f
                    for f in scoped_facts
                    if f.kind == kind
                    and f.scope_type == prefs.amenity_scope
                    and f.state == "operating"
                    and f.relation == "within"
                ]
                scope_label = "в бигвилле" if prefs.amenity_scope == "bigville" else "в ЖК"
                if matching:
                    reasons.append(f"Застройщик указывает: {matching[0].name} {scope_label}")
                else:
                    tradeoffs.append(
                        f"{AMENITY_NAMES[kind]}: действующий объект {scope_label} пока не подтверждён"
                    )
                continue
            eligible = [
                a
                for a in amenities
                if a.kind == kind
                and a.state == "operating"
                and aware(a.observed_at) >= utcnow() - timedelta(days=180)
                and a.distance_m <= prefs.amenity_radius_m
            ]
            if eligible:
                nearby = min(eligible, key=lambda a: a.distance_m)
                reasons.append(f"{AMENITY_NAMES[kind]} — {nearby.distance_m} м по прямой")
            else:
                tradeoffs.append(f"{AMENITY_NAMES[kind]}: наличие в нужном радиусе не подтверждено")
        if not fresh:
            tradeoffs.append("Наличие нужно уточнить у застройщика")
        if apartment.status != "available":
            tradeoffs.append(
                {
                    "sold": "По данным застройщика продана",
                    "reserved": "По данным застройщика забронирована",
                }.get(apartment.status, "Наличие не подтверждено")
            )
        return Listing(
            **{
                name: getattr(apartment, name)
                for name in [
                    "id",
                    "provider_id",
                    "complex_name",
                    "city",
                    "district",
                    "address",
                    "rooms",
                    "area_m2",
                    "floor",
                    "total_floors",
                    "price_kzt",
                    "status",
                    "latitude",
                    "longitude",
                    "finish",
                    "completion",
                    "source_url",
                    "image_url",
                    "version",
                ]
            },
            observed_at=aware(apartment.observed_at),
            provider_name=provider.name,
            provenance="demo" if provider.demo else "provider",
            freshness="recent" if fresh else "stale",
            bigville_name=apartment.bigville_name,
            project_facts=[
                ProjectFactOut(
                    id=f.id,
                    kind=f.kind,
                    name=f.name,
                    state=f.state,
                    relation=f.relation,
                    scope_type=f.scope_type,
                    expected_opening=f.expected_opening,
                    evidence=f.evidence,
                    source_url=f.source_url,
                    observed_at=aware(f.observed_at),
                )
                for f in scoped_facts
            ],
            reasons=reasons,
            tradeoffs=tradeoffs,
            amenities=[
                AmenityOut(
                    kind=a.kind,
                    name=a.name,
                    distance_m=a.distance_m,
                    source_url=a.source_url,
                    observed_at=aware(a.observed_at),
                )
                for a in sorted(amenities, key=lambda a: a.distance_m)
                if a.state == "operating" and aware(a.observed_at) >= utcnow() - timedelta(days=180)
            ],
        )

    async def verify(self, apartment_id: str) -> dict | None:
        async with self.db.sessions() as session:
            row = await session.get(Apartment, apartment_id)
        if not row or row.provider_id not in self.providers:
            return None
        result = await self.refresh_provider(
            self.providers[row.provider_id], external_id=row.external_id
        )
        listing = await self.get(apartment_id)
        confirmed = (
            result["status"] in {"complete", "recently_checked"}
            and listing
            and listing.freshness == "recent"
            and listing.provenance == "provider"
        )
        return {
            "listing": listing.model_dump(mode="json") if listing else None,
            "verification": "confirmed" if confirmed else "unconfirmed",
            "checked_at": utcnow().isoformat(),
        }
