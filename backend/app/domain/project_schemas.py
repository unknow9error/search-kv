"""Public-data contract for projects; it does not imply available sales stock."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.domain.project_input import (
    ProjectBuildingInput,
    ProjectDocumentInput,
    ProjectImageInput,
    ProjectStage,
    ProjectWebsiteScope,
)
from app.domain.project_input import (
    ProjectInput as ProjectInput,
)
from app.domain.project_input import (
    ProjectLayoutInput as ProjectLayoutInput,
)
from app.domain.project_input import (
    PublicURL as PublicURL,
)
from app.domain.project_input import (
    SourceDate as SourceDate,
)
from app.domain.schemas import (
    AmenityKind,
    Listing,
    ProjectFactOut,
    StrictModel,
    normalize_city,
)

ProjectPriceKind = Literal["published_starting_price", "observed_listing_minimum", "unknown"]
ProjectPriceMode = Literal["published_starting_price", "observed_listing_minimum"]
ProjectSort = Literal["price_asc", "price_desc", "name", "observed_desc"]


class ProjectBounds(StrictModel):
    south: float = Field(ge=-90, le=90, allow_inf_nan=False)
    west: float = Field(ge=-180, le=180, allow_inf_nan=False)
    north: float = Field(ge=-90, le=90, allow_inf_nan=False)
    east: float = Field(ge=-180, le=180, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self):
        if self.south > self.north or self.west > self.east:
            raise ValueError("Bounds must be ordered south/north and west/east")
        return self


class ProjectCriteria(StrictModel):
    city: str | None = Field(default=None, min_length=1, max_length=80)
    q: str | None = Field(default=None, min_length=1, max_length=200)
    districts: list[str] = Field(default_factory=list, max_length=50)
    developer_names: list[str] = Field(default_factory=list, max_length=50)
    provider_ids: list[str] = Field(default_factory=list, max_length=50)
    price_mode: ProjectPriceMode = "published_starting_price"
    price_min: int | None = Field(default=None, ge=1, le=10_000_000_000)
    price_max: int | None = Field(default=None, ge=1, le=10_000_000_000)
    stages: list[ProjectStage] = Field(default_factory=list, max_length=4)
    completion_before: date | None = None
    rooms: list[int] = Field(default_factory=list, max_length=8)
    area_min: float | None = Field(default=None, ge=1, le=2000, allow_inf_nan=False)
    floor_min: int | None = Field(default=None, ge=1, le=150)
    floor_max: int | None = Field(default=None, ge=1, le=150)
    required_amenities: list[AmenityKind] = Field(default_factory=list, max_length=4)
    amenity_scope: Literal["nearby", "complex", "bigville"] = "nearby"
    amenity_radius_m: int = Field(default=1000, ge=100, le=5000)
    include_stale: bool = False
    bounds: ProjectBounds | None = None

    _city = field_validator("city")(normalize_city)

    @field_validator("rooms")
    @classmethod
    def room_values(cls, values):
        if any(room < 1 or room > 8 for room in values):
            raise ValueError("Rooms must be 1–8")
        return sorted(set(values))

    @field_validator("districts", "developer_names", "provider_ids", "stages")
    @classmethod
    def bounded_labels(cls, values):
        if any(not value.strip() or len(value) > 200 for value in values):
            raise ValueError("Filter values must be non-empty and at most 200 characters")
        return sorted({value.strip() for value in values})

    @field_validator("required_amenities")
    @classmethod
    def unique_amenities(cls, values):
        return sorted(set(values))

    @model_validator(mode="after")
    def ranges(self):
        if self.price_min is not None and self.price_max is not None and self.price_min > self.price_max:
            raise ValueError("Minimum price exceeds maximum price")
        if self.floor_min is not None and self.floor_max is not None and self.floor_min > self.floor_max:
            raise ValueError("Minimum floor exceeds maximum floor")
        return self


class ProjectSearchRequest(StrictModel):
    criteria: ProjectCriteria = Field(default_factory=ProjectCriteria)
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=4096)
    sort: ProjectSort = "observed_desc"


class ProjectPrice(StrictModel):
    kind: ProjectPriceKind
    amount_kzt: int | None = Field(default=None, ge=1, le=10_000_000_000)
    source_url: str | None = None
    observed_at: datetime | None = None

    @model_validator(mode="after")
    def evidence(self):
        if self.kind == "unknown":
            if any(value is not None for value in (self.amount_kzt, self.source_url, self.observed_at)):
                raise ValueError("Unknown price cannot contain an amount or evidence")
        elif self.amount_kzt is None or self.source_url is None or self.observed_at is None:
            raise ValueError("Known price requires amount, source and observation date")
        return self


class ProjectLayoutOut(StrictModel):
    id: str
    project_id: str
    external_id: str
    name: str
    rooms: int | None = None
    area_m2: float | None = None
    image_url: str | None = None
    source_url: str
    observed_at: datetime
    received_at: datetime
    provenance: Literal["demo", "provider"]
    freshness: Literal["recent", "stale"]
    kind: Literal["layout_type"] = "layout_type"


class ProjectAmenityOut(StrictModel):
    kind: AmenityKind
    name: str
    state: Literal["operating", "planned", "under_construction", "unknown"]
    distance_m: int = Field(ge=0)
    source_url: str
    observed_at: datetime
    distance_type: Literal["straight_line"] = "straight_line"


class ProjectOut(StrictModel):
    id: str
    provider_id: str
    provider_name: str
    external_id: str
    name: str
    city: str
    district: str = ""
    address: str | None = None
    developer_name: str | None = None
    bigville_id: str | None = None
    bigville_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    source_url: str
    website_url: str | None = None
    website_scope: ProjectWebsiteScope | None = None
    observed_at: datetime
    received_at: datetime
    version: int
    record_origin: Literal["public_project", "observed_lots"]
    provenance: Literal["demo", "provider"]
    freshness: Literal["recent", "stale"]
    stage: ProjectStage | None = None
    completion: str | None = None
    completion_date: date | None = None
    finish: str | None = None
    published_starting_price: ProjectPrice | None = None
    observed_listing_minimum: ProjectPrice | None = None
    display_price: ProjectPrice = Field(default_factory=lambda: ProjectPrice(kind="unknown"))
    buildings: list[ProjectBuildingInput] = Field(default_factory=list)
    images: list[ProjectImageInput] = Field(default_factory=list)
    documents: list[ProjectDocumentInput] = Field(default_factory=list)
    layouts: list[ProjectLayoutOut] = Field(default_factory=list)
    published_lot_count: int = Field(default=0, ge=0)
    matched_lot_count: int = Field(default=0, ge=0)
    available_layout_count: int = Field(default=0, ge=0)
    amenities: list[ProjectAmenityOut] = Field(default_factory=list)
    project_facts: list[ProjectFactOut] = Field(default_factory=list)

    @model_validator(mode="after")
    def website_pair(self):
        if (self.website_url is None) != (self.website_scope is None):
            raise ValueError("Website URL and scope must be supplied together")
        return self


class ProjectPage(StrictModel):
    items: list[ProjectOut]
    total: int = Field(ge=0)
    next_cursor: str | None = None
    criteria: ProjectCriteria
    sort: ProjectSort
    unknown_coordinates_count: int = Field(default=0, ge=0)


class ProjectMapResponse(ProjectPage):
    pass


class ProjectApartmentPage(StrictModel):
    items: list[Listing]
    total: int = Field(ge=0)
    next_cursor: str | None = None


class ProjectLayoutPage(StrictModel):
    items: list[ProjectLayoutOut]


class ProjectFacetValue(StrictModel):
    value: str
    count: int = Field(ge=0)


class ProjectPriceFacetValue(StrictModel):
    value: ProjectPriceMode
    count: int = Field(ge=0)


class ProjectFacets(StrictModel):
    cities: list[ProjectFacetValue] = Field(default_factory=list)
    districts: list[ProjectFacetValue] = Field(default_factory=list)
    developer_names: list[ProjectFacetValue] = Field(default_factory=list)
    provider_ids: list[ProjectFacetValue] = Field(default_factory=list)
    stages: list[ProjectFacetValue] = Field(default_factory=list)
    price_modes: list[ProjectPriceFacetValue] = Field(default_factory=list, max_length=2)
    unknown_published_price_count: int = Field(default=0, ge=0)
    unknown_observed_price_count: int = Field(default=0, ge=0)

    @field_validator("price_modes")
    @classmethod
    def unique_price_modes(cls, values):
        if len({item.value for item in values}) != len(values):
            raise ValueError("Price modes must be distinct")
        return values


class ProjectCompareRequest(StrictModel):
    project_ids: list[UUID] = Field(min_length=2, max_length=3)

    @field_validator("project_ids")
    @classmethod
    def unique_ids(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Select distinct projects for comparison")
        return values


class ProjectComparisonValue(StrictModel):
    project_id: str
    value: str | int | float | None = None
    source_url: str | None = None
    observed_at: datetime | None = None


class ProjectComparisonRow(StrictModel):
    key: str
    label: str
    values: list[ProjectComparisonValue]


class ProjectCompareResponse(StrictModel):
    projects: list[ProjectOut]
    rows: list[ProjectComparisonRow]


class ProjectConversationCreate(StrictModel):
    criteria: ProjectCriteria = Field(default_factory=ProjectCriteria)
    client_conversation_id: UUID | None = None


class ProjectTurnRequest(StrictModel):
    client_turn_id: UUID
    message: str = Field(min_length=1, max_length=2000)
    criteria: ProjectCriteria | None = None
    selected_project_ids: list[UUID] = Field(default_factory=list, max_length=3)
    action: Literal["search", "compare", "explain"] = "search"

    @model_validator(mode="after")
    def selection(self):
        if len(set(self.selected_project_ids)) != len(self.selected_project_ids):
            raise ValueError("Selected project IDs must be distinct")
        if self.action == "compare" and len(self.selected_project_ids) < 2:
            raise ValueError("Compare requires at least two selected projects")
        if self.action == "explain" and not self.selected_project_ids:
            raise ValueError("Explain requires a selected project")
        return self


class ProjectCitation(StrictModel):
    project_id: str
    title: str
    url: str
    observed_at: datetime
    demo: bool


class ProjectTurnResponse(StrictModel):
    conversation_id: UUID
    turn_id: UUID
    client_turn_id: UUID
    mode: Literal["basic"] = "basic"
    state: Literal["complete"] = "complete"
    action: Literal["search", "compare", "explain", "clarify"] = "search"
    message: str
    criteria: ProjectCriteria
    results: ProjectPage | None = None
    comparison: ProjectCompareResponse | None = None
    citations: list[ProjectCitation] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    unsupported_conditions: list[str] = Field(default_factory=list)
    created_at: datetime


class ProjectConversationOut(StrictModel):
    id: UUID
    title: str
    criteria: ProjectCriteria
    created_at: datetime
    updated_at: datetime


class ProjectConversationPage(StrictModel):
    items: list[ProjectConversationOut]


class ProjectHistoryTurn(StrictModel):
    id: UUID
    client_turn_id: UUID
    message: str
    state: Literal["complete", "failed", "interrupted"]
    created_at: datetime
    response: ProjectTurnResponse | None = None


class ProjectConversationHistory(ProjectConversationOut):
    turns: list[ProjectHistoryTurn]
    has_more: bool = False
    next_before: UUID | None = None


DataReportCategory = Literal["price", "address", "completion", "image", "layout", "other"]


class DataReportRequest(StrictModel):
    client_report_id: UUID
    project_id: UUID
    category: DataReportCategory
    message: str = Field(min_length=1, max_length=2000)


class DataReportOut(StrictModel):
    id: UUID
    client_report_id: UUID
    project_id: UUID
    category: DataReportCategory
    message: str
    status: Literal["received"] = "received"
    created_at: datetime


class DataReportPage(StrictModel):
    items: list[DataReportOut]


class CatalogSource(StrictModel):
    provider_id: str
    name: str
    source_url: str | None = None
    website_url: str | None = None
    website_scope: ProjectWebsiteScope | None = None
    provenance: Literal["demo", "provider"]
    project_count: int = Field(ge=0)
    public_project_count: int = Field(ge=0)
    observed_lot_project_count: int = Field(ge=0)
    apartment_count: int = Field(ge=0)
    cities: list[str] = Field(default_factory=list)
    last_snapshot_at: datetime | None = None
    last_success_at: datetime | None = None
    latest_observed_at: datetime | None = None
    recent_project_count: int = Field(default=0, ge=0)
    stale_project_count: int = Field(default=0, ge=0)
    last_import_outcome: str | None = None


class CatalogSources(StrictModel):
    items: list[CatalogSource]
