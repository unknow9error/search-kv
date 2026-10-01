"""Explicit OSM import, separate from user search and LLM calls."""

import asyncio
import json
from datetime import datetime
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, select

from app.domain.models import Amenity, Apartment, Place
from app.domain.schemas import POIInput, distance_m
from app.providers.base import SourceError


def near_places(latitude, longitude, places):
    if latitude is None or longitude is None:
        return []
    nearest = {}
    for place in places:
        if abs(place.latitude - latitude) > 0.05 or abs(place.longitude - longitude) > 0.1:
            continue
        distance = distance_m(latitude, longitude, place.latitude, place.longitude)
        if distance <= 5000 and (place.kind not in nearest or distance < nearest[place.kind][1]):
            nearest[place.kind] = (place, distance)
    return list(nearest.values())


async def refresh_city(db, city: str, endpoint: str):
    url = urlsplit(endpoint)
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("Configure an HTTPS Overpass endpoint")
    async with db.sessions() as session:
        apartments = (
            await session.scalars(
                select(Apartment).where(Apartment.city == city, Apartment.latitude.is_not(None))
            )
        ).all()
    if not apartments:
        return 0
    south, north = min(x.latitude for x in apartments) - 0.05, max(x.latitude for x in apartments) + 0.05
    west, east = min(x.longitude for x in apartments) - 0.08, max(x.longitude for x in apartments) + 0.08
    if north - south > 0.8 or east - west > 1.2:
        raise ValueError("City bounding box is unexpectedly large; review provider coordinates")
    bbox = f"{south:.5f},{west:.5f},{north:.5f},{east:.5f}"
    query = f'[out:json][timeout:25];(nwr["amenity"~"^(school|kindergarten)$"]({bbox});nwr["leisure"="park"]({bbox});node["highway"="bus_stop"]({bbox}););out center tags;'
    async with httpx.AsyncClient(
        timeout=35,
        follow_redirects=False,
        trust_env=False,
        headers={
            "User-Agent": "Meken/1.0 (apartment surroundings import; cached, no end-user requests)"
        },
    ) as client:
        async with (
            asyncio.timeout(40),
            client.stream("POST", endpoint, data={"data": query}) as response,
        ):
            if response.status_code != 200:
                raise SourceError("places_source_unavailable")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 10_000_000:
                    raise SourceError("places_response_too_large")
    payload = json.loads(data)
    if payload.get("remark") or not isinstance(payload.get("elements"), list):
        raise SourceError("places_incomplete_response")
    observed = datetime.fromisoformat(payload["osm3s"]["timestamp_osm_base"].replace("Z", "+00:00"))
    parsed = []
    for element in payload["elements"]:
        tags, point = element.get("tags", {}), element.get("center", element)
        if tags.get("disused") == "yes" or tags.get("construction") or tags.get("proposed"):
            continue
        kind = tags.get("amenity")
        if kind not in {"school", "kindergarten"}:
            kind = (
                "park"
                if tags.get("leisure") == "park"
                else "transit"
                if tags.get("highway") == "bus_stop"
                else None
            )
        if not kind or element.get("type") not in {"node", "way", "relation"}:
            continue
        lat, lon = point.get("lat"), point.get("lon")
        if lat is None or lon is None or not (south <= lat <= north and west <= lon <= east):
            continue
        name = tags.get("name:ru") or tags.get("name")
        if not name:
            continue
        identifier = f"{element['type']}/{int(element['id'])}"
        poi = POIInput(
            kind=kind,
            state="operating",
            name=name[:200],
            latitude=lat,
            longitude=lon,
            observed_at=observed,
            source_url="https://www.openstreetmap.org/" + identifier,
        )
        parsed.append((identifier, poi))
    if not parsed:
        raise SourceError("places_empty_response_requires_review")
    async with db.sessions.begin() as session:
        await session.execute(delete(Place).where(Place.city == city))
        places = []
        for identifier, poi in parsed:
            place = Place(
                id=city + ":" + identifier,
                city=city,
                **poi.model_dump(mode="python", exclude={"source_url"}),
                source_url=str(poi.source_url),
            )
            session.add(place)
            places.append(place)
        # Refresh only OSM-derived evidence. Provider observations remain independent.
        ids = [a.id for a in apartments]
        await session.execute(
            delete(Amenity).where(
                Amenity.apartment_id.in_(ids), Amenity.source_url.like("https://www.openstreetmap.org/%")
            )
        )
        for apartment in apartments:
            for place, distance in near_places(apartment.latitude, apartment.longitude, places):
                session.add(
                    Amenity(
                        apartment_id=apartment.id,
                        kind=place.kind,
                        state=place.state,
                        name=place.name,
                        latitude=place.latitude,
                        longitude=place.longitude,
                        distance_m=distance,
                        source_url=place.source_url,
                        observed_at=observed,
                    )
                )
    return len(parsed)


async def main():
    import argparse

    from app.core.config import get_settings
    from app.core.db import Database

    parser = argparse.ArgumentParser()
    parser.add_argument("--city", required=True)
    parser.add_argument(
        "--endpoint",
        required=True,
        help="Overpass HTTPS endpoint; commercial deployments need a suitable hosted/self-hosted service",
    )
    args = parser.parse_args()
    db = Database(get_settings().database_url)
    try:
        print({"city": args.city, "imported_places": await refresh_city(db, args.city, args.endpoint)})
    finally:
        await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
