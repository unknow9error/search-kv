"""Read-only project catalog backed by independently sourced public project records.

Public reads never refresh providers. Lot projections are evidence about observed lots,
not the developer's complete inventory, project asking price, or project photographs.
"""

import base64
import hashlib
import json
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import BigInteger, and_, case, cast, exists, func, literal, or_, select, text
from sqlalchemy.orm import aliased

from app.core.config import Settings
from app.core.coordination import Coordination
from app.core.db import Database
from app.domain.models import Amenity, Apartment, ImportRun, Place, ProjectFact, Provider, aware, utcnow
from app.domain.project_models import ProjectLayoutRecord, ProjectRecord
from app.domain.project_schemas import (
    CatalogSources,
    ProjectApartmentPage,
    ProjectCompareResponse,
    ProjectComparisonRow,
    ProjectComparisonValue,
    ProjectCriteria,
    ProjectFacets,
    ProjectInput,
    ProjectLayoutOut,
    ProjectMapResponse,
    ProjectOut,
    ProjectPage,
    ProjectPrice,
    ProjectSearchRequest,
)
from app.domain.schemas import Preferences, ProjectFactOut, distance_m
from app.providers.base import SourceError


def project_id(provider_id: str, external_id: str) -> str:
    identity = json.dumps([provider_id, external_id], ensure_ascii=False, separators=(",", ":"))
    return str(uuid5(NAMESPACE_URL, "meken:project:" + identity))


def layout_id(provider_id: str, external_project_id: str, external_layout_id: str) -> str:
    identity = json.dumps(
        [provider_id, external_project_id, external_layout_id], ensure_ascii=False, separators=(",", ":")
    )
    return str(uuid5(NAMESPACE_URL, "meken:layout:" + identity))


def _content_hash(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _lot_project_key():
    return case(
        (and_(Apartment.complex_id.is_not(None), Apartment.complex_id != ""), Apartment.complex_id),
        else_=literal("listing:") + Apartment.external_id,
    )


def _lot_external_id(item) -> str:
    if isinstance(item, str):
        return item
    return item.complex_id or "listing:" + item.external_id


async def upsert_public_projects(
    session, provider_id: str, inputs: list[ProjectInput], as_of: datetime
) -> int:
    """Import public metadata inside the caller's provider-locked transaction.

    A repeated identical observation is a no-op. Conflicting bytes at the same
    observation time abort the batch; old imports cannot change newer metadata.
    Missing project/layout records in a partial publication are retained.
    """
    as_of = aware(as_of)
    if len({item.external_id for item in inputs}) != len(inputs):
        raise SourceError("duplicate_project_ids")
    if await session.get(Provider, provider_id) is None:
        raise SourceError("unknown_provider")
    existing = {
        row.external_id: row
        for row in (
            await session.scalars(
                select(ProjectRecord).where(
                    ProjectRecord.provider_id == provider_id,
                    ProjectRecord.external_id.in_([item.external_id for item in inputs]),
                )
            )
        ).all()
    }
    accepted = 0
    now = utcnow()
    for item in inputs:
        payload = item.model_dump(mode="json")
        observed_at = aware(item.observed_at)
        if observed_at > as_of:
            raise SourceError("project_observation_after_snapshot")
        # Layout types have their own observations. Their bytes and dates must
        # never invalidate or advance the independent project-core observation.
        fingerprint = _content_hash(
            {k: v for k, v in payload.items() if k not in {"observed_at", "layouts"}}
        )
        row = existing.get(item.external_id)
        write_parent = (
            row is None or row.record_origin != "public_project" or aware(row.observed_at) < observed_at
        )
        if row and row.record_origin == "public_project" and aware(row.observed_at) == observed_at:
            if row.content_hash != fingerprint:
                raise SourceError("project_observation_conflict")
        changed = write_parent
        if write_parent:
            core_keys = {
                "external_id",
                "name",
                "city",
                "district",
                "address",
                "developer_name",
                "latitude",
                "longitude",
                "source_url",
                "observed_at",
                "layouts",
            }
            values = {key: payload.get(key) for key in core_keys - {"observed_at", "layouts"}}
            values.update(
                source_url=str(item.source_url),
                observed_at=observed_at,
                received_at=now,
                record_origin="public_project",
                content_hash=fingerprint,
                data={key: value for key, value in payload.items() if key not in core_keys},
            )
            values["data"]["_search_text"] = " ".join(
                value for value in (item.name, item.address, item.district, item.developer_name) if value
            ).casefold()
            if row:
                for key, value in values.items():
                    setattr(row, key, value)
                row.version += 1
            else:
                row = ProjectRecord(
                    id=project_id(provider_id, item.external_id), provider_id=provider_id, **values
                )
                session.add(row)
                existing[item.external_id] = row
            await session.flush()
        stored_layouts = {
            record.external_id: record
            for record in (
                await session.scalars(
                    select(ProjectLayoutRecord).where(
                        ProjectLayoutRecord.project_id == row.id,
                    )
                )
            ).all()
        }
        for layout in item.layouts:
            layout_payload = layout.model_dump(mode="json")
            layout_observed = aware(layout.observed_at)
            if layout_observed > as_of:
                raise SourceError("layout_observation_after_snapshot")
            stored = stored_layouts.get(layout.external_id)
            if stored and aware(stored.observed_at) > layout_observed:
                continue
            if stored and aware(stored.observed_at) == layout_observed:
                if stored.data != layout_payload:
                    raise SourceError("layout_observation_conflict")
                continue
            layout_values = {
                "name": layout.name,
                "rooms": layout.rooms,
                "area_m2": layout.area_m2,
                "image_url": str(layout.image_url) if layout.image_url else None,
                "source_url": str(layout.source_url),
                "observed_at": layout_observed,
                "received_at": now,
                "data": layout_payload,
            }
            if stored:
                for key, value in layout_values.items():
                    setattr(stored, key, value)
            else:
                record = ProjectLayoutRecord(
                    id=layout_id(provider_id, item.external_id, layout.external_id),
                    project_id=row.id,
                    external_id=layout.external_id,
                    **layout_values,
                )
                session.add(record)
                stored_layouts[layout.external_id] = record
            changed = True
        accepted += int(changed)

    return accepted


async def project_projection_from_lots(session, provider_id: str, items, as_of: datetime) -> int:
    """Create source-scoped projects from accepted stored lots without inventing metadata."""
    keys = {_lot_external_id(item) for item in items}
    if not keys:
        return 0
    ranked = (
        select(
            Apartment,
            func.row_number()
            .over(
                partition_by=_lot_project_key(),
                order_by=(Apartment.observed_at.desc(), Apartment.id),
            )
            .label("position"),
        )
        .where(
            Apartment.provider_id == provider_id,
            _lot_project_key().in_(keys),
        )
        .subquery()
    )
    ranked_apartment = aliased(Apartment, ranked)
    records = (await session.scalars(select(ranked_apartment).where(ranked.c.position == 1))).all()
    representatives = {}
    for apartment in records:
        representatives.setdefault(_lot_external_id(apartment), apartment)
    projects = {
        row.external_id: row
        for row in (
            await session.scalars(
                select(ProjectRecord).where(
                    ProjectRecord.provider_id == provider_id,
                    ProjectRecord.external_id.in_(keys),
                )
            )
        ).all()
    }
    # PostgreSQL groups expressions by their actual parameter identities.
    # Reuse one CASE object so SELECT, WHERE and GROUP BY share bind slots.
    location_key = _lot_project_key()
    location_stats = (
        await session.execute(
            select(
                location_key.label("external_id"),
                func.count(func.distinct(func.nullif(Apartment.address, ""))).label("addresses"),
                func.count(func.distinct(func.nullif(Apartment.district, ""))).label("districts"),
                func.count(func.distinct(Apartment.latitude)).label("latitudes"),
                func.count(func.distinct(Apartment.longitude)).label("longitudes"),
            )
            .where(Apartment.provider_id == provider_id, location_key.in_(keys))
            .group_by(location_key)
        )
    ).all()
    locations = {item.external_id: item for item in location_stats}
    accepted = 0
    for external_id, apartment in representatives.items():
        row = projects.get(external_id)
        if row and row.record_origin == "public_project":
            continue
        observed = aware(apartment.observed_at)
        if row and aware(row.observed_at) > observed:
            continue
        location = locations[external_id]
        coordinates_consistent = location.latitudes <= 1 and location.longitudes <= 1
        values = {
            "external_id": external_id,
            "name": apartment.complex_name,
            "city": apartment.city,
            "district": (apartment.district or "") if location.districts <= 1 else "",
            "address": (apartment.address or None) if location.addresses <= 1 else None,
            "developer_name": None,
            "latitude": apartment.latitude if coordinates_consistent else None,
            "longitude": apartment.longitude if coordinates_consistent else None,
            "source_url": apartment.source_url,
            "data": {
                "bigville_id": apartment.bigville_id,
                "bigville_name": apartment.bigville_name,
            },
        }
        values["data"]["_search_text"] = " ".join(
            value for value in (apartment.complex_name, values["address"], values["district"]) if value
        ).casefold()
        fingerprint = _content_hash(values)
        if row and aware(row.observed_at) == observed and row.content_hash == fingerprint:
            continue
        values.update(
            observed_at=observed,
            received_at=aware(apartment.received_at),
            record_origin="observed_lots",
            content_hash=fingerprint,
        )
        if row:
            for key, value in values.items():
                setattr(row, key, value)
            row.version += 1
        else:
            session.add(
                ProjectRecord(id=project_id(provider_id, external_id), provider_id=provider_id, **values)
            )
        accepted += 1
    return accepted


async def bootstrap_legacy_projects(db: Database, catalog, settings: Settings) -> int:
    """Materialize missing projects from existing public stored lots on startup.

    No source calls or invented observations. Each provider's bounded chunk is
    locked atomically; repeat startup is a no-op. This helper must not run on GET.
    """
    public_ids = [
        source.id
        for source in catalog.providers.values()
        if getattr(source, "public_data", False)
        or (settings.env == "demo" and getattr(source, "demo", False))
    ]
    accepted = 0
    for provider_id in public_ids:
        while True:
            async with db.sessions.begin() as session:
                if db.engine.dialect.name == "sqlite":
                    # SQLite has no row locks. Take the write reservation before
                    # selecting missing rows so another local importer cannot race.
                    await session.execute(text("BEGIN IMMEDIATE"))
                provider = await session.scalar(
                    select(Provider)
                    .where(
                        Provider.id == provider_id,
                        Provider.enabled.is_(True),
                    )
                    .with_for_update()
                )
                if provider is None or (provider.demo and settings.env != "demo"):
                    break
                key = _lot_project_key()
                already_stored = exists(
                    select(ProjectRecord.id)
                    .where(
                        ProjectRecord.provider_id == Apartment.provider_id,
                        ProjectRecord.external_id == key,
                    )
                    .correlate(Apartment)
                )
                keys = (
                    await session.scalars(
                        select(key)
                        .where(
                            Apartment.provider_id == provider_id,
                            ~already_stored,
                        )
                        .distinct()
                        .order_by(key)
                        .limit(500)
                    )
                ).all()
                if not keys:
                    break
                accepted += await project_projection_from_lots(session, provider_id, keys, utcnow())
    return accepted


class Projects:
    def __init__(self, db: Database, coordination: Coordination, catalog, settings: Settings):
        self.db, self.coordination, self.catalog, self.settings = db, coordination, catalog, settings

    def _visibility(self):
        public_ids = [
            source.id
            for source in self.catalog.providers.values()
            if getattr(source, "public_data", False)
            or (self.settings.env == "demo" and getattr(source, "demo", False))
        ]
        conditions = [Provider.enabled.is_(True), Provider.id.in_(public_ids)]
        if self.settings.env != "demo":
            conditions.append(Provider.demo.is_(False))
        return conditions

    def _eligible_lots(self, now):
        return [
            Apartment.status == "available",
            Apartment.observed_at >= now - timedelta(seconds=self.settings.max_catalog_age_seconds),
        ]

    def _lot_constraints(self, criteria: ProjectCriteria, now, price=True):
        conditions = self._eligible_lots(now)
        if criteria.rooms:
            conditions.append(Apartment.rooms.in_(criteria.rooms))
        if criteria.area_min is not None:
            conditions.append(Apartment.area_m2 >= criteria.area_min)
        if criteria.floor_min is not None:
            conditions.append(Apartment.floor >= criteria.floor_min)
        if criteria.floor_max is not None:
            conditions.append(Apartment.floor <= criteria.floor_max)
        if price and criteria.price_mode == "observed_listing_minimum":
            if criteria.price_min is not None:
                conditions.append(Apartment.price_kzt >= criteria.price_min)
            if criteria.price_max is not None:
                conditions.append(Apartment.price_kzt <= criteria.price_max)
        return conditions

    def _lot_stats(self, criteria, now, matched=False):
        project_key = _lot_project_key()
        return (
            select(
                Apartment.provider_id.label("provider_id"),
                project_key.label("external_id"),
                func.count(Apartment.id).label("lot_count"),
                func.min(Apartment.price_kzt).label("minimum_price"),
            )
            .where(*self._lot_constraints(criteria, now, price=matched))
            .group_by(
                Apartment.provider_id,
                project_key,
            )
            .subquery()
        )

    def _amenity_exists(self, kind: str, criteria: ProjectCriteria, now):
        if criteria.amenity_scope in {"complex", "bigville"}:
            scope_id = (
                ProjectRecord.external_id
                if criteria.amenity_scope == "complex"
                else ProjectRecord.data["bigville_id"].as_string()
            )
            return exists(
                select(ProjectFact.id).where(
                    ProjectFact.provider_id == ProjectRecord.provider_id,
                    ProjectFact.scope_type == criteria.amenity_scope,
                    ProjectFact.scope_id == scope_id,
                    ProjectFact.kind == kind,
                    ProjectFact.state == "operating",
                    ProjectFact.relation == "within",
                    ProjectFact.expires_at > now,
                )
            )
        # Lot amenities contain independently sourced distances. Coordinates from
        # the public project may also support a fresh public Place directly.
        lot_evidence = exists(
            select(Amenity.id)
            .join(Apartment, Amenity.apartment_id == Apartment.id)
            .where(
                Apartment.provider_id == ProjectRecord.provider_id,
                _lot_project_key() == ProjectRecord.external_id,
                Amenity.kind == kind,
                Amenity.state == "operating",
                Amenity.observed_at >= now - timedelta(days=180),
                Amenity.distance_m <= criteria.amenity_radius_m,
                *self._eligible_lots(now),
            )
        )
        # Equirectangular upper precision is not used as evidence. A bounded SQL
        # spherical distance makes the radius filter consistent across SQL engines.
        # SQLite's math functions are bundled with the standard runtime.
        lat1 = ProjectRecord.latitude * literal(0.017453292519943295)
        lat2 = Place.latitude * literal(0.017453292519943295)
        delta = (Place.longitude - ProjectRecord.longitude) * literal(0.017453292519943295)
        cosine = func.sin(lat1) * func.sin(lat2) + func.cos(lat1) * func.cos(lat2) * func.cos(delta)
        clamped = case((cosine > 1.0, 1.0), (cosine < -1.0, -1.0), else_=cosine)
        place_evidence = exists(
            select(Place.id).where(
                Place.city == ProjectRecord.city,
                Place.kind == kind,
                Place.state == "operating",
                Place.observed_at >= now - timedelta(days=180),
                ProjectRecord.latitude.is_not(None),
                ProjectRecord.longitude.is_not(None),
                func.acos(clamped) * 6371000 <= criteria.amenity_radius_m,
            )
        )
        return or_(lot_evidence, place_evidence)

    def _statement(self, criteria: ProjectCriteria, now, map_only=False, spatial=True):
        empty = ProjectCriteria()
        stats = self._lot_stats(empty, now)
        matched = self._lot_stats(criteria, now, matched=True)
        published_key = _lot_project_key()
        published = (
            select(
                Apartment.provider_id.label("provider_id"),
                published_key.label("external_id"),
                func.count(Apartment.id).label("lot_count"),
            )
            .group_by(Apartment.provider_id, published_key)
            .subquery()
        )
        statement = (
            select(
                ProjectRecord,
                Provider,
                func.coalesce(stats.c.lot_count, 0).label("lot_count"),
                stats.c.minimum_price.label("minimum_price"),
                func.coalesce(matched.c.lot_count, 0).label("matched_count"),
                func.coalesce(published.c.lot_count, 0).label("published_count"),
            )
            .join(Provider, ProjectRecord.provider_id == Provider.id)
            .outerjoin(
                stats,
                and_(
                    stats.c.provider_id == ProjectRecord.provider_id,
                    stats.c.external_id == ProjectRecord.external_id,
                ),
            )
            .outerjoin(
                matched,
                and_(
                    matched.c.provider_id == ProjectRecord.provider_id,
                    matched.c.external_id == ProjectRecord.external_id,
                ),
            )
            .outerjoin(
                published,
                and_(
                    published.c.provider_id == ProjectRecord.provider_id,
                    published.c.external_id == ProjectRecord.external_id,
                ),
            )
            .where(*self._visibility())
        )
        if not criteria.include_stale:
            statement = statement.where(
                ProjectRecord.observed_at
                >= now - timedelta(seconds=self.settings.max_catalog_age_seconds)
            )
        if criteria.city:
            statement = statement.where(ProjectRecord.city == criteria.city)
        if criteria.q:
            pattern = (
                "%" + criteria.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            )
            statement = statement.where(
                or_(
                    ProjectRecord.data["_search_text"].as_string().like(pattern.casefold(), escape="\\"),
                    ProjectRecord.name.ilike(pattern, escape="\\"),
                    ProjectRecord.address.ilike(pattern, escape="\\"),
                    ProjectRecord.district.ilike(pattern, escape="\\"),
                )
            )
        for field, values in (
            (ProjectRecord.district, criteria.districts),
            (ProjectRecord.developer_name, criteria.developer_names),
            (ProjectRecord.provider_id, criteria.provider_ids),
        ):
            if values:
                statement = statement.where(field.in_(values))
        if criteria.stages:
            statement = statement.where(ProjectRecord.data["stage"].as_string().in_(criteria.stages))
        if criteria.completion_before:
            statement = statement.where(
                ProjectRecord.data["completion_date"].as_string()
                <= criteria.completion_before.isoformat()
            )
        if criteria.price_mode == "published_starting_price":
            selected_price = cast(
                ProjectRecord.data["published_starting_price_kzt"].as_string(), BigInteger
            )
            if criteria.price_min is not None:
                statement = statement.where(selected_price >= criteria.price_min)
            if criteria.price_max is not None:
                statement = statement.where(selected_price <= criteria.price_max)
        else:
            selected_price = stats.c.minimum_price
        has_lot_filter = (
            bool(criteria.rooms)
            or any(
                value is not None
                for value in (
                    criteria.area_min,
                    criteria.floor_min,
                    criteria.floor_max,
                )
            )
            or (
                criteria.price_mode == "observed_listing_minimum"
                and (criteria.price_min is not None or criteria.price_max is not None)
            )
        )
        if has_lot_filter:
            statement = statement.where(matched.c.lot_count > 0)
        for kind in criteria.required_amenities:
            statement = statement.where(self._amenity_exists(kind, criteria, now))
        if spatial and criteria.bounds:
            bounds = criteria.bounds
            statement = statement.where(
                ProjectRecord.latitude.between(bounds.south, bounds.north),
                ProjectRecord.longitude.between(bounds.west, bounds.east),
            )
        if map_only:
            statement = statement.where(
                ProjectRecord.latitude.is_not(None), ProjectRecord.longitude.is_not(None)
            )
        return statement, selected_price

    @staticmethod
    def _cursor_fingerprint(criteria: ProjectCriteria, sort: str, scope="projects") -> str:
        return _content_hash(
            {"criteria": criteria.model_dump(mode="json"), "sort": sort, "scope": scope}
        )

    def _cursor(self, value: str | None, criteria: ProjectCriteria, sort: str, scope="projects"):
        if value is None:
            return None
        try:
            if len(value) > 2048:
                raise ValueError()
            payload = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
            if not isinstance(payload, dict):
                raise ValueError()
            if payload.get("v") != 1 or payload.get("hash") != self._cursor_fingerprint(
                criteria, sort, scope
            ):
                raise ValueError()
            UUID(payload["id"])
            key = payload["key"]
            if sort in {"price_asc", "price_desc"}:
                if key is not None and (not isinstance(key, int) or isinstance(key, bool) or key < 0):
                    raise ValueError()
            elif sort == "observed_desc":
                key = aware(datetime.fromisoformat(key))
            elif sort == "name" and (not isinstance(key, str) or len(key) > 300):
                raise ValueError()
            return key, payload["id"]
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("invalid_cursor") from None

    def _encode_cursor(self, row, criteria, sort, scope="projects"):
        key = row[-1]
        if isinstance(key, datetime):
            key = aware(key).isoformat()
        return (
            base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "v": 1,
                        "hash": self._cursor_fingerprint(criteria, sort, scope),
                        "key": key,
                        "id": row[0].id,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            )
            .rstrip(b"=")
            .decode()
        )

    @staticmethod
    def _paginate(statement, key, sort, cursor):
        descending = sort in {"price_desc", "observed_desc"}
        if cursor:
            value, record_id = cursor
            if value is None:
                condition = and_(key.is_(None), ProjectRecord.id > record_id)
            else:
                ahead = key < value if descending else key > value
                condition = or_(key.is_(None), ahead, and_(key == value, ProjectRecord.id > record_id))
            statement = statement.where(condition)
        return statement.order_by(
            key.is_(None).asc(), key.desc() if descending else key.asc(), ProjectRecord.id
        )

    async def _page(self, request: ProjectSearchRequest, map_only=False):
        now = utcnow()
        criteria = request.criteria
        statement, price = self._statement(criteria, now, map_only=map_only)
        scope = "map" if map_only else "projects"
        cursor = self._cursor(request.cursor, criteria, request.sort, scope)
        key = {
            "price_asc": price,
            "price_desc": price,
            "name": ProjectRecord.name,
            "observed_desc": ProjectRecord.observed_at,
        }[request.sort]
        async with self.db.sessions() as session:
            total = await session.scalar(select(func.count()).select_from(statement.subquery()))
            unbounded, _ = self._statement(criteria, now, spatial=False)
            unknown = await session.scalar(
                select(func.count()).select_from(
                    unbounded.where(
                        or_(
                            ProjectRecord.latitude.is_(None),
                            ProjectRecord.longitude.is_(None),
                        )
                    ).subquery()
                )
            )
            paged = self._paginate(
                statement.add_columns(key.label("sort_key")), key, request.sort, cursor
            ).limit(request.limit + 1)
            rows = (await session.execute(paged)).all()
            more = len(rows) > request.limit
            rows = rows[: request.limit]
            items = await self._present_rows(session, rows, criteria.price_mode, now)
        return ProjectPage(
            items=items,
            total=total,
            next_cursor=self._encode_cursor(rows[-1], criteria, request.sort, scope) if more else None,
            criteria=criteria,
            sort=request.sort,
            unknown_coordinates_count=unknown,
        )

    async def search(self, request: ProjectSearchRequest) -> ProjectPage:
        return await self._page(request)

    async def map(self, request: ProjectSearchRequest) -> ProjectMapResponse:
        page = await self._page(request, map_only=True)
        return ProjectMapResponse(**page.model_dump())

    async def get(self, record_id: str) -> ProjectOut | None:
        now = utcnow()
        statement, _ = self._statement(ProjectCriteria(include_stale=True), now)
        async with self.db.sessions() as session:
            rows = (await session.execute(statement.where(ProjectRecord.id == record_id))).all()
            items = await self._present_rows(session, rows, "published_starting_price", now, full=True)
        return items[0] if items else None

    async def get_many(self, ids: list[str], *, full: bool = False) -> list[ProjectOut]:
        """Read up to 200 saved project IDs in one batch, keeping caller order."""
        if len(ids) > 200:
            raise ValueError("invalid_limit")
        ids = [str(value) for value in ids]
        if not ids:
            return []
        now = utcnow()
        statement, _ = self._statement(ProjectCriteria(include_stale=True), now)
        async with self.db.sessions() as session:
            rows = (await session.execute(statement.where(ProjectRecord.id.in_(ids)))).all()
            items = await self._present_rows(session, rows, "published_starting_price", now, full=full)
        indexed = {item.id: item for item in items}
        return [indexed[record_id] for record_id in ids if record_id in indexed]

    def _freshness(self, observed_at: datetime, now: datetime) -> str:
        return (
            "recent"
            if (now - aware(observed_at)).total_seconds() <= self.settings.fresh_seconds
            else "stale"
        )

    def _layout_out(self, row, provider, now):
        return ProjectLayoutOut(
            id=row.id,
            project_id=row.project_id,
            external_id=row.external_id,
            name=row.name,
            rooms=row.rooms,
            area_m2=row.area_m2,
            image_url=row.image_url,
            source_url=row.source_url,
            observed_at=aware(row.observed_at),
            received_at=aware(row.received_at),
            provenance="demo" if provider.demo else "provider",
            freshness=self._freshness(row.observed_at, now),
        )

    async def _present_rows(self, session, rows, price_mode, now, full=False):
        if not rows:
            return []
        from sqlalchemy.orm import aliased

        from app.domain.project_schemas import ProjectAmenityOut

        records = [row[0] for row in rows]
        project_ids = [record.id for record in records]
        scope = or_(
            *(
                and_(
                    Apartment.provider_id == record.provider_id, _lot_project_key() == record.external_id
                )
                for record in records
            )
        )
        ranked = (
            select(
                Apartment,
                func.row_number()
                .over(
                    partition_by=(Apartment.provider_id, _lot_project_key()),
                    order_by=(Apartment.price_kzt, Apartment.id),
                )
                .label("position"),
            )
            .where(scope, *self._eligible_lots(now))
            .subquery()
        )
        ranked_apartment = aliased(Apartment, ranked)
        minimum_lots = (
            await session.scalars(select(ranked_apartment).where(ranked.c.position == 1))
        ).all()
        minimum_by_project = {(a.provider_id, _lot_external_id(a)): a for a in minimum_lots}
        layout_counts = dict(
            (
                await session.execute(
                    select(
                        ProjectLayoutRecord.project_id,
                        func.count(ProjectLayoutRecord.id),
                    )
                    .where(ProjectLayoutRecord.project_id.in_(project_ids))
                    .group_by(ProjectLayoutRecord.project_id)
                )
            ).all()
        )
        layout_rows = (
            (
                await session.scalars(
                    select(ProjectLayoutRecord)
                    .where(
                        ProjectLayoutRecord.project_id.in_(project_ids),
                    )
                    .order_by(
                        ProjectLayoutRecord.rooms, ProjectLayoutRecord.area_m2, ProjectLayoutRecord.id
                    )
                )
            ).all()
            if full
            else []
        )
        layouts_by_project = {}
        for layout in layout_rows:
            layouts_by_project.setdefault(layout.project_id, []).append(layout)
        fact_scope = or_(
            *(
                and_(
                    ProjectFact.provider_id == record.provider_id,
                    or_(
                        and_(
                            ProjectFact.scope_type == "complex",
                            ProjectFact.scope_id == record.external_id,
                        ),
                        and_(
                            ProjectFact.scope_type == "bigville",
                            ProjectFact.scope_id == record.data.get("bigville_id"),
                        ),
                    ),
                )
                for record in records
            )
        )
        facts = (
            await session.scalars(
                select(ProjectFact)
                .where(
                    fact_scope,
                    ProjectFact.expires_at > now,
                )
                .order_by(ProjectFact.observed_at.desc(), ProjectFact.id)
            )
        ).all()
        # Distinct evidence rows, rather than every duplicate lot observation.
        amenity_rows = (
            await session.execute(
                select(
                    Apartment.provider_id,
                    _lot_project_key().label("project_external_id"),
                    Amenity.kind,
                    Amenity.state,
                    Amenity.name,
                    Amenity.distance_m,
                    Amenity.source_url,
                    Amenity.observed_at,
                )
                .join(Amenity, Amenity.apartment_id == Apartment.id)
                .where(
                    scope,
                    Amenity.observed_at >= now - timedelta(days=180),
                )
                .distinct()
            )
        ).all()
        import math

        place_scopes = []
        for record in records:
            if record.latitude is None or record.longitude is None:
                continue
            latitude_delta = 5000 / 111000
            longitude_delta = 5000 / (111000 * max(0.001, math.cos(math.radians(record.latitude))))
            place_scopes.append(
                and_(
                    Place.city == record.city,
                    Place.latitude.between(
                        record.latitude - latitude_delta, record.latitude + latitude_delta
                    ),
                    Place.longitude.between(
                        record.longitude - longitude_delta, record.longitude + longitude_delta
                    ),
                )
            )
        nearby_places = (
            (
                await session.scalars(
                    select(Place).where(
                        or_(*place_scopes),
                        Place.observed_at >= now - timedelta(days=180),
                    )
                )
            ).all()
            if place_scopes
            else []
        )
        output = []
        for row in rows:
            record, provider, _, _, matched_count, published_count = row[:6]
            data = record.data or {}
            published_amount = data.get("published_starting_price_kzt")
            published = (
                ProjectPrice(
                    kind="published_starting_price",
                    amount_kzt=published_amount,
                    source_url=record.source_url,
                    observed_at=aware(record.observed_at),
                )
                if published_amount is not None
                else None
            )
            minimum_lot = minimum_by_project.get((record.provider_id, record.external_id))
            observed = (
                ProjectPrice(
                    kind="observed_listing_minimum",
                    amount_kzt=minimum_lot.price_kzt,
                    source_url=minimum_lot.source_url,
                    observed_at=aware(minimum_lot.observed_at),
                )
                if minimum_lot
                else None
            )
            chosen = published if price_mode == "published_starting_price" else observed
            scoped_facts = [
                fact
                for fact in facts
                if fact.provider_id == record.provider_id
                and (
                    (fact.scope_type == "complex" and fact.scope_id == record.external_id)
                    or (fact.scope_type == "bigville" and fact.scope_id == data.get("bigville_id"))
                )
            ]
            amenities = {}
            for amenity in amenity_rows:
                if (
                    amenity.provider_id == record.provider_id
                    and amenity.project_external_id == record.external_id
                ):
                    key = (amenity.kind, amenity.name, amenity.source_url, amenity.state)
                    candidate = ProjectAmenityOut(
                        kind=amenity.kind,
                        state=amenity.state,
                        name=amenity.name,
                        distance_m=amenity.distance_m,
                        source_url=amenity.source_url,
                        observed_at=aware(amenity.observed_at),
                    )
                    previous = amenities.get(key)
                    if previous is None or candidate.observed_at > previous.observed_at:
                        amenities[key] = candidate
            if record.latitude is not None and record.longitude is not None:
                for place in nearby_places:
                    if place.city != record.city:
                        continue
                    distance = distance_m(
                        record.latitude, record.longitude, place.latitude, place.longitude
                    )
                    if distance > 5000:
                        continue
                    key = (place.kind, place.name, place.source_url, place.state)
                    candidate = ProjectAmenityOut(
                        kind=place.kind,
                        state=place.state,
                        name=place.name,
                        distance_m=distance,
                        source_url=place.source_url,
                        observed_at=aware(place.observed_at),
                    )
                    previous = amenities.get(key)
                    if previous is None or candidate.observed_at > previous.observed_at:
                        amenities[key] = candidate
            project_layouts = [
                self._layout_out(layout, provider, now)
                for layout in layouts_by_project.get(record.id, [])
            ]
            output.append(
                ProjectOut(
                    id=record.id,
                    provider_id=record.provider_id,
                    provider_name=provider.name,
                    external_id=record.external_id,
                    name=record.name,
                    city=record.city,
                    district=record.district or "",
                    address=record.address,
                    developer_name=record.developer_name,
                    latitude=record.latitude,
                    longitude=record.longitude,
                    source_url=record.source_url,
                    observed_at=aware(record.observed_at),
                    received_at=aware(record.received_at),
                    version=record.version,
                    record_origin=record.record_origin,
                    provenance="demo" if provider.demo else "provider",
                    freshness=self._freshness(record.observed_at, now),
                    stage=data.get("stage"),
                    completion=data.get("completion"),
                    completion_date=data.get("completion_date"),
                    finish=data.get("finish"),
                    published_starting_price=published,
                    observed_listing_minimum=observed,
                    display_price=chosen or ProjectPrice(kind="unknown"),
                    bigville_id=data.get("bigville_id"),
                    bigville_name=data.get("bigville_name"),
                    website_url=data.get("website_url"),
                    website_scope=data.get("website_scope"),
                    images=data.get("images", [])
                    if full
                    else self._summary_images(data.get("images", [])),
                    buildings=data.get("buildings", []) if full else [],
                    documents=data.get("documents", []) if full else [],
                    layouts=project_layouts,
                    available_layout_count=layout_counts.get(record.id, 0),
                    published_lot_count=published_count,
                    matched_lot_count=matched_count,
                    amenities=sorted(
                        amenities.values(), key=lambda item: (item.distance_m, item.kind, item.name)
                    ),
                    project_facts=[
                        ProjectFactOut(
                            id=fact.id,
                            kind=fact.kind,
                            name=fact.name,
                            state=fact.state,
                            relation=fact.relation,
                            scope_type=fact.scope_type,
                            expected_opening=fact.expected_opening,
                            evidence=fact.evidence,
                            source_url=fact.source_url,
                            observed_at=aware(fact.observed_at),
                        )
                        for fact in scoped_facts[: None if full else 12]
                    ],
                )
            )
        return output

    @staticmethod
    def _summary_images(images):
        if not images:
            return []
        preferred = next((item for item in images if item.get("kind") in {"facade", "site"}), images[0])
        return [preferred]

    async def facets(self, city: str | None = None) -> ProjectFacets:
        from app.domain.project_schemas import ProjectFacetValue, ProjectPriceFacetValue

        now = utcnow()
        statement, _ = self._statement(ProjectCriteria(city=city), now)
        base = statement.with_only_columns(
            ProjectRecord.city.label("city"),
            ProjectRecord.district.label("district"),
            ProjectRecord.developer_name.label("developer_name"),
            ProjectRecord.provider_id.label("provider_id"),
            ProjectRecord.data["stage"].as_string().label("stage"),
            cast(ProjectRecord.data["published_starting_price_kzt"].as_string(), BigInteger).label(
                "published_price"
            ),
        ).subquery()
        facets = {}
        async with self.db.sessions() as session:
            for output_name, column_name in (
                ("cities", "city"),
                ("districts", "district"),
                ("developer_names", "developer_name"),
                ("provider_ids", "provider_id"),
                ("stages", "stage"),
            ):
                column = base.c[column_name]
                values = (
                    await session.execute(
                        select(column, func.count())
                        .where(
                            column.is_not(None),
                            column != "",
                        )
                        .group_by(column)
                        .order_by(column)
                    )
                ).all()
                facets[output_name] = [
                    ProjectFacetValue(value=value, count=count) for value, count in values
                ]
            published_count = await session.scalar(
                select(func.count()).select_from(base).where(base.c.published_price.is_not(None))
            )
            observed_statement, price = self._statement(
                ProjectCriteria(city=city, price_mode="observed_listing_minimum"), now
            )
            observed_count = await session.scalar(
                select(func.count()).select_from(observed_statement.where(price.is_not(None)).subquery())
            )
            modes = []
            if published_count:
                modes.append(
                    ProjectPriceFacetValue(value="published_starting_price", count=published_count)
                )
            if observed_count:
                modes.append(
                    ProjectPriceFacetValue(value="observed_listing_minimum", count=observed_count)
                )
            unknown_count = await session.scalar(
                select(func.count()).select_from(base).where(base.c.published_price.is_(None))
            )
            unknown_observed_count = await session.scalar(
                select(func.count()).select_from(observed_statement.where(price.is_(None)).subquery())
            )
        return ProjectFacets(
            **facets,
            price_modes=modes,
            unknown_published_price_count=unknown_count,
            unknown_observed_price_count=unknown_observed_count,
        )

    async def compare(self, ids: list[str]) -> ProjectCompareResponse:
        ids = [str(value) for value in ids]
        if not 2 <= len(ids) <= 3 or len(set(ids)) != len(ids):
            raise ValueError("invalid_project_comparison")
        now = utcnow()
        statement, _ = self._statement(ProjectCriteria(include_stale=True), now)
        async with self.db.sessions() as session:
            rows = (await session.execute(statement.where(ProjectRecord.id.in_(ids)))).all()
            found = await self._present_rows(session, rows, "published_starting_price", now)
        indexed = {item.id: item for item in found}
        if any(record_id not in indexed for record_id in ids):
            raise ValueError("project_not_found")
        projects = [indexed[record_id] for record_id in ids]
        rows = []
        for key, label in (
            ("city", "Город"),
            ("district", "Район"),
            ("address", "Адрес"),
            ("developer_name", "Застройщик"),
            ("stage", "Опубликованная стадия"),
            ("completion", "Опубликованный срок сдачи"),
            ("finish", "Отделка"),
            ("published_starting_price", "Опубликованная цена от"),
            ("observed_listing_minimum", "Минимум среди наблюдавшихся лотов"),
            ("published_lot_count", "Опубликованные записи лотов"),
            ("available_layout_count", "Опубликованные типы планировок"),
        ):
            values = []
            for project in projects:
                value = getattr(project, key)
                source, observed_at = project.source_url, project.observed_at
                if key in {"published_starting_price", "observed_listing_minimum"}:
                    source = value.source_url if value else None
                    observed_at = value.observed_at if value else None
                    value = value.amount_kzt if value else None
                if value in {None, ""}:
                    value, source, observed_at = None, None, None
                values.append(
                    ProjectComparisonValue(
                        project_id=project.id,
                        value=value,
                        source_url=source,
                        observed_at=observed_at,
                    )
                )
            rows.append(ProjectComparisonRow(key=key, label=label, values=values))
        return ProjectCompareResponse(projects=projects, rows=rows)

    async def layouts(self, record_id: str) -> list[ProjectLayoutOut]:
        now = utcnow()
        async with self.db.sessions() as session:
            rows = (
                await session.execute(
                    select(ProjectLayoutRecord, Provider)
                    .join(
                        ProjectRecord,
                        ProjectLayoutRecord.project_id == ProjectRecord.id,
                    )
                    .join(Provider, ProjectRecord.provider_id == Provider.id)
                    .where(
                        ProjectRecord.id == record_id,
                        *self._visibility(),
                    )
                    .order_by(
                        ProjectLayoutRecord.rooms, ProjectLayoutRecord.area_m2, ProjectLayoutRecord.id
                    )
                )
            ).all()
        return [self._layout_out(layout, provider, now) for layout, provider in rows]

    async def layout(self, record_id: str) -> ProjectLayoutOut | None:
        now = utcnow()
        async with self.db.sessions() as session:
            row = (
                await session.execute(
                    select(ProjectLayoutRecord, Provider)
                    .join(
                        ProjectRecord,
                        ProjectLayoutRecord.project_id == ProjectRecord.id,
                    )
                    .join(Provider, ProjectRecord.provider_id == Provider.id)
                    .where(
                        ProjectLayoutRecord.id == record_id,
                        *self._visibility(),
                    )
                )
            ).first()
        return self._layout_out(*row, now) if row else None

    async def apartments(
        self, record_id: str, limit: int = 20, cursor: str | None = None
    ) -> ProjectApartmentPage | None:
        if not 1 <= limit <= 100:
            raise ValueError("invalid_limit")
        now = utcnow()
        async with self.db.sessions() as session:
            project = await session.scalar(
                select(ProjectRecord)
                .join(Provider)
                .where(
                    ProjectRecord.id == record_id,
                    *self._visibility(),
                )
            )
            if project is None:
                return None
            scope = "apartments:" + record_id
            criteria = ProjectCriteria()
            pagination = self._cursor(cursor, criteria, "price_asc", scope)
            statement = (
                select(Apartment, Provider)
                .join(Provider)
                .where(
                    Apartment.provider_id == project.provider_id,
                    _lot_project_key() == project.external_id,
                    *self._eligible_lots(now),
                    *self._visibility(),
                )
            )
            total = await session.scalar(select(func.count()).select_from(statement.subquery()))
            if pagination:
                price, apartment_id = pagination
                if price is None:
                    raise ValueError("invalid_cursor")
                statement = statement.where(
                    or_(
                        Apartment.price_kzt > price,
                        and_(Apartment.price_kzt == price, Apartment.id > apartment_id),
                    )
                )
            rows = (
                await session.execute(
                    statement.order_by(Apartment.price_kzt, Apartment.id).limit(limit + 1)
                )
            ).all()
            more = len(rows) > limit
            rows = rows[:limit]
            amenities = (
                (
                    await session.scalars(
                        select(Amenity).where(
                            Amenity.apartment_id.in_([row[0].id for row in rows]),
                        )
                    )
                ).all()
                if rows
                else []
            )
            facts = await self.catalog.project_facts(session, [row[0] for row in rows])
            by_apartment = {}
            for amenity in amenities:
                by_apartment.setdefault(amenity.apartment_id, []).append(amenity)
            items = [
                self.catalog.present(
                    apartment, provider, by_apartment.get(apartment.id, []), Preferences(), facts
                )
                for apartment, provider in rows
            ]
            next_cursor = (
                self._encode_cursor((rows[-1][0], rows[-1][0].price_kzt), criteria, "price_asc", scope)
                if more
                else None
            )
        return ProjectApartmentPage(items=items, total=total, next_cursor=next_cursor)

    async def sources(self) -> CatalogSources:
        from app.domain.project_schemas import CatalogSource

        now = utcnow()
        async with self.db.sessions() as session:
            providers = (
                await session.scalars(
                    select(Provider).where(*self._visibility()).order_by(Provider.name, Provider.id)
                )
            ).all()
            ids = [provider.id for provider in providers]
            project_stats = (
                await session.execute(
                    select(
                        ProjectRecord.provider_id,
                        func.count(ProjectRecord.id),
                        func.sum(case((ProjectRecord.record_origin == "public_project", 1), else_=0)),
                        func.sum(case((ProjectRecord.record_origin == "observed_lots", 1), else_=0)),
                        func.max(ProjectRecord.observed_at),
                        func.sum(
                            case(
                                (
                                    ProjectRecord.observed_at
                                    >= now - timedelta(seconds=self.settings.fresh_seconds),
                                    1,
                                ),
                                else_=0,
                            )
                        ),
                    )
                    .where(ProjectRecord.provider_id.in_(ids))
                    .group_by(ProjectRecord.provider_id)
                )
            ).all()
            stats_by_provider = {row[0]: row[1:] for row in project_stats}
            ranked_sources = (
                select(
                    ProjectRecord.provider_id,
                    ProjectRecord.source_url,
                    ProjectRecord.data["website_url"].as_string().label("website_url"),
                    ProjectRecord.data["website_scope"].as_string().label("website_scope"),
                    func.row_number()
                    .over(
                        partition_by=ProjectRecord.provider_id,
                        order_by=(ProjectRecord.observed_at.desc(), ProjectRecord.id),
                    )
                    .label("position"),
                )
                .where(ProjectRecord.provider_id.in_(ids))
                .subquery()
            )
            source_rows = (
                await session.execute(
                    select(
                        ranked_sources.c.provider_id,
                        ranked_sources.c.source_url,
                        ranked_sources.c.website_url,
                        ranked_sources.c.website_scope,
                    ).where(ranked_sources.c.position == 1)
                )
            ).all()
            observed_sources = {row[0]: row[1:] for row in source_rows}
            lot_counts = dict(
                (
                    await session.execute(
                        select(Apartment.provider_id, func.count())
                        .where(
                            Apartment.provider_id.in_(ids),
                        )
                        .group_by(Apartment.provider_id)
                    )
                ).all()
            )
            city_rows = (
                await session.execute(
                    select(ProjectRecord.provider_id, ProjectRecord.city)
                    .where(
                        ProjectRecord.provider_id.in_(ids),
                    )
                    .distinct()
                    .order_by(ProjectRecord.city)
                )
            ).all()
            cities = {}
            for provider_id, city in city_rows:
                cities.setdefault(provider_id, []).append(city)
            success_rows = dict(
                (
                    await session.execute(
                        select(ImportRun.provider_id, func.max(ImportRun.started_at))
                        .where(
                            ImportRun.provider_id.in_(ids),
                            ImportRun.outcome == "success",
                        )
                        .group_by(ImportRun.provider_id)
                    )
                ).all()
            )
            latest_import = (
                select(
                    ImportRun.provider_id,
                    ImportRun.outcome,
                    func.row_number()
                    .over(
                        partition_by=ImportRun.provider_id,
                        order_by=(ImportRun.started_at.desc(), ImportRun.id.desc()),
                    )
                    .label("position"),
                )
                .where(ImportRun.provider_id.in_(ids))
                .subquery()
            )
            outcomes = dict(
                (
                    await session.execute(
                        select(latest_import.c.provider_id, latest_import.c.outcome).where(
                            latest_import.c.position == 1
                        )
                    )
                ).all()
            )
        items = []
        for provider in providers:
            configured = self.catalog.providers[provider.id]
            observation_source, website_url, website_scope = observed_sources.get(
                provider.id, (None, None, None)
            )
            source_url = (
                getattr(getattr(configured, "config", None), "base_url", None)
                or getattr(configured, "origin", None)
                or observation_source
            )
            if getattr(configured, "website_url", None) and getattr(configured, "website_scope", None):
                website_url, website_scope = configured.website_url, configured.website_scope
            count, public_count, derived_count, latest_observed, recent_count = stats_by_provider.get(
                provider.id, (0, 0, 0, None, 0)
            )
            items.append(
                CatalogSource(
                    provider_id=provider.id,
                    name=provider.name,
                    source_url=str(source_url) if source_url else None,
                    website_url=str(website_url) if website_url else None,
                    website_scope=website_scope if website_url else None,
                    provenance="demo" if provider.demo else "provider",
                    project_count=count,
                    public_project_count=public_count,
                    observed_lot_project_count=derived_count,
                    apartment_count=lot_counts.get(provider.id, 0),
                    cities=cities.get(provider.id, []),
                    last_snapshot_at=aware(provider.last_snapshot_at)
                    if provider.last_snapshot_at
                    else None,
                    last_success_at=aware(success_rows[provider.id])
                    if provider.id in success_rows
                    else None,
                    latest_observed_at=aware(latest_observed) if latest_observed else None,
                    recent_project_count=recent_count,
                    stale_project_count=count - recent_count,
                    last_import_outcome=outcomes.get(provider.id),
                )
            )
        return CatalogSources(items=items)
