"""Validated public project snapshots, independent from legacy listing schemas."""

from datetime import UTC, date, datetime, timedelta
from ipaddress import ip_address
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def normalize_city(value: str) -> str:
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


def validate_source_date(value: datetime) -> datetime:
    if (
        value.tzinfo is None
        or value.utcoffset() is None
        or value > datetime.now(UTC) + timedelta(minutes=2)
    ):
        raise ValueError("Timestamp must be timezone-aware and not in the future")
    return value


def validate_public_url(value: HttpUrl) -> HttpUrl:
    if value.scheme != "https" or value.username is not None or value.password is not None:
        raise ValueError("Public URLs must use HTTPS without credentials")
    host = (value.host or "").strip("[]").casefold().rstrip(".")
    if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Public URLs cannot name a local host")
    try:
        address = ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("Public URLs cannot use a private or reserved IP address")
    return value


PublicURL = Annotated[HttpUrl, AfterValidator(validate_public_url)]
SourceDate = Annotated[datetime, AfterValidator(validate_source_date)]
ProjectStage = Literal["planned", "under_construction", "commissioned", "unknown"]
ProjectWebsiteScope = Literal["project", "provider_catalog"]


class ProjectBuildingInput(StrictModel):
    external_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    stage: ProjectStage | None = None
    completion: str | None = Field(default=None, max_length=100)
    completion_date: date | None = None
    total_floors: int | None = Field(default=None, ge=1, le=150)
    source_url: PublicURL
    observed_at: SourceDate


class ProjectImageInput(StrictModel):
    url: PublicURL
    kind: Literal["facade", "site", "interior", "other"] = "other"
    caption: str | None = Field(default=None, max_length=300)
    source_url: PublicURL
    observed_at: SourceDate


class ProjectDocumentInput(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    url: PublicURL
    kind: Literal["permit", "declaration", "brochure", "other"] = "other"
    source_url: PublicURL
    observed_at: SourceDate


class ProjectLayoutInput(StrictModel):
    external_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    rooms: int | None = Field(default=None, ge=1, le=8)
    area_m2: float | None = Field(default=None, ge=1, le=2000, allow_inf_nan=False)
    image_url: PublicURL | None = None
    source_url: PublicURL
    observed_at: SourceDate


class ProjectInput(StrictModel):
    external_id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=80)
    district: str = Field(default="", max_length=120)
    address: str | None = Field(default=None, max_length=300)
    developer_name: str | None = Field(default=None, min_length=1, max_length=200)
    bigville_id: str | None = Field(default=None, min_length=1, max_length=120)
    bigville_name: str | None = Field(default=None, min_length=1, max_length=200)
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    source_url: PublicURL
    website_url: PublicURL | None = None
    website_scope: ProjectWebsiteScope | None = None
    observed_at: SourceDate
    published_starting_price_kzt: int | None = Field(default=None, ge=1, le=10_000_000_000)
    stage: ProjectStage | None = None
    completion: str | None = Field(default=None, min_length=1, max_length=100)
    completion_date: date | None = None
    finish: str | None = Field(default=None, min_length=1, max_length=120)
    buildings: list[ProjectBuildingInput] = Field(default_factory=list, max_length=100)
    images: list[ProjectImageInput] = Field(default_factory=list, max_length=100)
    documents: list[ProjectDocumentInput] = Field(default_factory=list, max_length=50)
    layouts: list[ProjectLayoutInput] = Field(default_factory=list, max_length=500)

    _city = field_validator("city")(normalize_city)

    @field_validator("external_id")
    @classmethod
    def reject_reserved_namespace(cls, value):
        if value.startswith("listing:"):
            raise ValueError("The listing: namespace is reserved for derived singleton projects")
        return value

    @model_validator(mode="after")
    def validate_observation(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Coordinates must be supplied as a pair")
        if (self.website_url is None) != (self.website_scope is None):
            raise ValueError("Website URL and scope must be supplied together")
        for records in (self.buildings, self.layouts):
            if len({record.external_id for record in records}) != len(records):
                raise ValueError("Duplicate external IDs in project metadata")
        return self
