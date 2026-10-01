import math
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Availability(StrEnum):
    available = "available"
    reserved = "reserved"
    sold = "sold"
    unknown = "unknown"


AmenityKind = Literal["school", "kindergarten", "park", "transit"]


def normalize_city(value: str | None) -> str | None:
    if value is None:
        return None
    aliases = {
        "астана": "Астана",
        "astana": "Астана",
        "нур-султан": "Астана",
        "алматы": "Алматы",
        "almaty": "Алматы",
        "шымкент": "Шымкент",
        "shymkent": "Шымкент",
        "атырау": "Атырау",
        "актау": "Актау",
        "караганда": "Караганда",
    }
    return aliases.get(value.strip().casefold(), value.strip())


class Preferences(StrictModel):
    city: str | None = Field(default=None, max_length=80)
    budget_max: int | None = Field(default=None, ge=1_000_000, le=10_000_000_000)
    rooms: list[int] = Field(default_factory=list, max_length=8)
    area_min: float | None = Field(default=None, ge=10, le=1000)
    floor_min: int | None = Field(default=None, ge=1, le=150)
    floor_max: int | None = Field(default=None, ge=1, le=150)
    preferred_amenities: list[AmenityKind] = Field(default_factory=list, max_length=4)
    required_amenities: list[AmenityKind] = Field(default_factory=list, max_length=4)
    amenity_radius_m: int = Field(default=1000, ge=100, le=5000)
    amenity_scope: Literal["nearby", "complex", "bigville"] = "nearby"

    _city = field_validator("city")(normalize_city)

    @field_validator("rooms")
    @classmethod
    def validate_rooms(cls, values):
        if any(x < 1 or x > 8 for x in values):
            raise ValueError("Rooms must be 1–8")
        return sorted(set(values))

    @model_validator(mode="after")
    def floor_range(self):
        if self.floor_min and self.floor_max and self.floor_min > self.floor_max:
            raise ValueError("Minimum floor exceeds maximum floor")
        return self


class POIInput(StrictModel):
    kind: AmenityKind
    state: Literal["operating", "planned", "under_construction", "unknown"] = "unknown"
    name: str = Field(min_length=1, max_length=200)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    source_url: HttpUrl
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def timestamp(cls, value):
        if value.tzinfo is None or value > datetime.now(UTC) + timedelta(minutes=2):
            raise ValueError("Timestamp must be timezone-aware and not in the future")
        return value


class ListingInput(StrictModel):
    external_id: str = Field(min_length=1, max_length=120, pattern=r"^[\w.\-]+$")
    complex_name: str = Field(min_length=1, max_length=200)
    complex_id: str | None = Field(default=None, max_length=120)
    bigville_id: str | None = Field(default=None, max_length=120)
    bigville_name: str | None = Field(default=None, max_length=200)
    city: str = Field(min_length=1, max_length=80)
    district: str = Field(default="", max_length=120)
    address: str = Field(min_length=1, max_length=300)
    rooms: int = Field(ge=1, le=8)
    area_m2: float = Field(ge=10, le=2000)
    floor: int = Field(ge=1, le=150)
    total_floors: int = Field(ge=1, le=150)
    price_kzt: int = Field(ge=1_000_000, le=10_000_000_000)
    status: Availability
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    finish: str = Field(default="Не указана", max_length=120)
    completion: str = Field(default="Уточняется", max_length=100)
    source_url: HttpUrl
    image_url: HttpUrl | None = None
    amenities: list[POIInput] = Field(default_factory=list, max_length=30)

    _city = field_validator("city")(normalize_city)

    @model_validator(mode="after")
    def check(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Coordinates must be supplied as a pair")
        if self.amenities and self.latitude is None:
            raise ValueError("Amenities require verified apartment coordinates")
        if self.floor > self.total_floors:
            raise ValueError("Floor exceeds building height")
        if self.source_url.scheme != "https" or (self.image_url and self.image_url.scheme != "https"):
            raise ValueError("Public URLs must use HTTPS")
        return self


class Snapshot(StrictModel):
    as_of: datetime
    complete: bool
    items: list[ListingInput]
    allow_empty: bool = False
    scope_city: str | None = None
    project_contexts: list["ProjectContextInput"] = Field(default_factory=list, max_length=500)

    _timestamp = field_validator("as_of")(POIInput.timestamp.__func__)
    _city = field_validator("scope_city")(normalize_city)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({x.external_id for x in self.items}) != len(self.items):
            raise ValueError("Duplicate external IDs in snapshot")
        if self.complete and not self.items and not self.allow_empty:
            raise ValueError("Empty complete snapshot needs explicit allow_empty")
        if self.scope_city and any(x.city != self.scope_city for x in self.items):
            raise ValueError("Snapshot contains records outside declared city")
        return self


class AmenityOut(StrictModel):
    kind: AmenityKind
    name: str
    distance_m: int
    source_url: str
    observed_at: datetime
    distance_type: Literal["straight_line"] = "straight_line"


class Listing(StrictModel):
    id: str
    provider_id: str
    provider_name: str
    complex_name: str
    city: str
    district: str
    address: str
    rooms: int
    area_m2: float
    floor: int
    total_floors: int
    price_kzt: int
    status: Availability
    latitude: float | None
    longitude: float | None
    finish: str
    completion: str
    source_url: str
    image_url: str | None
    observed_at: datetime
    version: int
    provenance: Literal["demo", "provider"]
    freshness: Literal["recent", "stale"]
    reasons: list[str]
    tradeoffs: list[str]
    amenities: list[AmenityOut]
    bigville_name: str | None = None
    project_facts: list["ProjectFactOut"] = Field(default_factory=list)


class ProjectFactInput(StrictModel):
    kind: AmenityKind
    name: str = Field(min_length=1, max_length=200)
    state: Literal["operating", "planned", "under_construction", "unknown"]
    relation: Literal["within", "nearby", "unspecified"] = "unspecified"
    expected_opening: str | None = Field(default=None, max_length=100)
    evidence: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def reject_future_as_operating(self):
        import re

        if self.state == "operating" and re.search(
            r"запланир|планиру|построят|строится|появится|будет\s+(?:постро|откры)|предусмотрено\s+строительство",
            self.evidence.casefold(),
        ):
            raise ValueError("Future/construction evidence cannot establish an operating facility")
        return self


class ProjectContextInput(StrictModel):
    provider_id: str = Field(min_length=1, max_length=80)
    scope_type: Literal["complex", "bigville"]
    scope_id: str = Field(min_length=1, max_length=120)
    source_url: HttpUrl
    observed_at: datetime
    expires_at: datetime
    facts: list[ProjectFactInput] = Field(max_length=50)

    _timestamp = field_validator("observed_at")(POIInput.timestamp.__func__)

    @model_validator(mode="after")
    def verify_context(self):
        if (
            self.source_url.scheme != "https"
            or self.expires_at.tzinfo is None
            or self.expires_at <= self.observed_at
        ):
            raise ValueError("Context needs HTTPS source and a valid expiry")
        if self.expires_at > self.observed_at + timedelta(days=180):
            raise ValueError("Review infrastructure context at least every 180 days")
        return self


class ProjectFactOut(StrictModel):
    id: str
    kind: AmenityKind
    name: str
    state: Literal["operating", "planned", "under_construction", "unknown"]
    relation: Literal["within", "nearby", "unspecified"] = "unspecified"
    scope_type: Literal["complex", "bigville"]
    expected_opening: str | None
    evidence: str
    source_url: str
    observed_at: datetime


class SearchRequest(StrictModel):
    preferences: Preferences = Field(default_factory=Preferences)
    limit: int = Field(default=20, ge=1, le=50)


class TurnRequest(StrictModel):
    client_turn_id: UUID
    message: str = Field(min_length=1, max_length=2000)
    selected_listing_ids: list[UUID] = Field(default_factory=list, max_length=3)


class ConversationRequest(StrictModel):
    preferences: Preferences = Field(default_factory=Preferences)


class RefreshRequest(StrictModel):
    refresh_token: str = Field(min_length=32, max_length=128)


class ListingPage(StrictModel):
    items: list[Listing]


class VerificationResponse(StrictModel):
    listing: Listing | None
    verification: Literal["confirmed", "unconfirmed"]
    checked_at: datetime


class TokenResponse(StrictModel):
    access_token: str
    refresh_token: str
    expires_in: int
    user_id: str


class AppConfiguration(StrictModel):
    mode: Literal["demo", "live"]
    ai_enabled: bool
    cities: list[str]
    privacy_url: str | None
    terms_url: str | None
    retention_days: int


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = phi2 - phi1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return round(6_371_000 * 2 * math.asin(min(1, math.sqrt(a))))
