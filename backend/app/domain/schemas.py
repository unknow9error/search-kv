import math
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    GetJsonSchemaHandler,
    HttpUrl,
    RootModel,
    field_validator,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from app.domain.project_input import ProjectInput


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
    projects: list[ProjectInput] = Field(default_factory=list, max_length=20000)

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
        if len({x.external_id for x in self.projects}) != len(self.projects):
            raise ValueError("Duplicate project external IDs in snapshot")
        if self.scope_city and any(x.city != self.scope_city for x in self.projects):
            raise ValueError("Snapshot contains projects outside declared city")
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
    client_conversation_id: UUID | None = Field(
        default=None,
        description="Persist before creation and reuse with identical initial preferences when retrying. "
        "The key is scoped to the authenticated user; a conflicting retry returns idempotency_conflict.",
    )


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
    capabilities: list[str] = Field(
        default_factory=list, description="Supported optional API features; absent means legacy."
    )


class ProfileResponse(StrictModel):
    id: UUID
    identity: Literal["anonymous"]
    retention_days: int


class ConversationSummary(StrictModel):
    id: UUID
    title: str
    preferences: Preferences


class ConversationListItem(ConversationSummary):
    updated_at: datetime


class ConversationPage(StrictModel):
    items: list[ConversationListItem]


TurnStatus = Literal["running", "complete", "failed", "interrupted"]
TerminalTurnStatus = Literal["complete", "failed", "interrupted"]


class AcceptedPayload(StrictModel):
    turn_id: UUID
    conversation_id: UUID


class TextPayload(StrictModel):
    text: str


class Citation(StrictModel):
    id: str
    title: str
    url: str
    demo: bool


class MessagePayload(TextPayload):
    citations: list[Citation]


class ListingsPayload(StrictModel):
    items: list[Listing]
    phase: Literal["catalog", "refresh"]
    replace: Literal[True] = Field(description="Replace the current result set, preserving item order.")


class SuggestionsPayload(StrictModel):
    items: list[str]


class ProviderPayload(StrictModel):
    provider_id: str
    name: str
    status: str = Field(
        description="Provider progress or source error code. Known progress values include batch, "
        "complete, recently_checked, updating, temporarily_unavailable and timeout. Other values "
        "describe source failures and must be handled as an incomplete refresh.",
        examples=["batch", "complete", "recently_checked", "invalid_schema", "unavailable"],
    )
    count: int = Field(
        ge=0, description="Records accepted for this batch or completed provider refresh."
    )


class StreamErrorPayload(TextPayload):
    code: Literal["turn_failed"]


class DonePayload(StrictModel):
    status: TerminalTurnStatus = Field(description="Only complete denotes successful completion.")
    turn_id: UUID


class PendingPayload(StrictModel):
    """Replay is a snapshot; poll again after pending to read later durable events."""


class HistoryEventBase(StrictModel):
    sequence: int = Field(ge=1)


class HistoryMessageEvent(HistoryEventBase):
    kind: Literal["message"]
    payload: MessagePayload


class HistoryPreferencesEvent(HistoryEventBase):
    kind: Literal["preferences"]
    payload: Preferences


class HistoryDoneEvent(HistoryEventBase):
    kind: Literal["done"]
    payload: DonePayload


class HistoryNoticeEvent(HistoryEventBase):
    kind: Literal["notice"]
    payload: TextPayload


class HistoryListingsEvent(HistoryEventBase):
    kind: Literal["listings"]
    payload: ListingsPayload


HistoryEvent = Annotated[
    HistoryMessageEvent
    | HistoryPreferencesEvent
    | HistoryDoneEvent
    | HistoryNoticeEvent
    | HistoryListingsEvent,
    Field(discriminator="kind"),
]


class HistoryTurn(StrictModel):
    id: UUID
    message: str
    status: TurnStatus
    events: list[HistoryEvent]


class ConversationHistory(ConversationSummary):
    has_more: bool
    next_before: UUID | None = Field(
        description="Cursor for the preceding page; null when has_more is false."
    )
    turns: list[HistoryTurn] = Field(
        description="Up to 30 turns in chronological order. Only message, preferences, done, notice "
        "and the latest listings result set in this page are retained in this response."
    )


class SSEEventBase(StrictModel):
    id: int = Field(
        ge=1, description="Durable sequence; pass it as the after query parameter on replay."
    )


class SSEAcceptedEvent(SSEEventBase):
    event: Literal["accepted"]
    data: AcceptedPayload


class SSEStatusEvent(SSEEventBase):
    event: Literal["status"]
    data: TextPayload


class SSENoticeEvent(SSEEventBase):
    event: Literal["notice"]
    data: TextPayload


class SSEPreferencesEvent(SSEEventBase):
    event: Literal["preferences"]
    data: Preferences


class SSEListingsEvent(SSEEventBase):
    event: Literal["listings"]
    data: ListingsPayload


class SSEMessageEvent(SSEEventBase):
    event: Literal["message"]
    data: MessagePayload


class SSESuggestionsEvent(SSEEventBase):
    event: Literal["suggestions"]
    data: SuggestionsPayload


class SSEProviderEvent(SSEEventBase):
    event: Literal["provider"]
    data: ProviderPayload


class SSEErrorEvent(SSEEventBase):
    event: Literal["error"]
    data: StreamErrorPayload


class SSEDoneEvent(SSEEventBase):
    id: int | None = Field(
        default=None, ge=1, description="Omitted for a terminal replay marker without a durable event."
    )
    event: Literal["done"]
    data: DonePayload


class SSEPendingEvent(StrictModel):
    id: None = Field(default=None, description="A pending replay marker has no durable sequence.")
    event: Literal["pending"]
    data: PendingPayload


SSEEvent = Annotated[
    SSEAcceptedEvent
    | SSEStatusEvent
    | SSENoticeEvent
    | SSEPreferencesEvent
    | SSEListingsEvent
    | SSEMessageEvent
    | SSESuggestionsEvent
    | SSEProviderEvent
    | SSEErrorEvent
    | SSEDoneEvent
    | SSEPendingEvent,
    Field(discriminator="event"),
]


class SSEStreamSchema(RootModel[SSEEvent]):
    """Documentation-only stream schema. The HTTP body is SSE text, not a JSON envelope."""

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        event_schema = handler(core_schema)
        event_schema.pop("title", None)
        return {
            "type": "string",
            "format": "text/event-stream",
            "title": cls.__name__,
            "description": "UTF-8 Server-Sent Events framed as id, event and JSON data fields, "
            "followed by a blank line. x-event-schema describes one parsed event. Unknown event "
            "names may be added; clients should ignore them. Replay uses after, not Last-Event-ID.",
            "x-event-schema": event_schema,
        }


class APIError(StrictModel):
    code: str = Field(description="Stable machine-readable error code; no request inputs are echoed.")


class APIErrorResponse(StrictModel):
    error: APIError


API_ERROR_RESPONSES = {
    401: {
        "model": APIErrorResponse,
        "description": "session_expired: missing, invalid or expired session.",
    },
    404: {
        "model": APIErrorResponse,
        "description": "apartment_not_found, conversation_not_found or turn_not_found. "
        "Resources owned by another user also return not found.",
    },
    409: {
        "model": APIErrorResponse,
        "description": "idempotency_conflict, turn_in_progress, favorites_limit, conversations_limit "
        "or conversation_limit. Reusing an idempotency key with a different request is a conflict.",
    },
    413: {
        "model": APIErrorResponse,
        "description": "request_too_large: request body exceeds the limit.",
    },
    422: {
        "model": APIErrorResponse,
        "description": "invalid_request: body or path/query validation failed.",
    },
    429: {
        "model": APIErrorResponse,
        "description": "rate_limited: wait for Retry-After seconds before retrying.",
        "headers": {"Retry-After": {"schema": {"type": "integer"}, "description": "Delay in seconds."}},
    },
    503: {"model": APIErrorResponse, "description": "temporarily_unavailable: retry with backoff."},
}
API_ERROR_RESPONSES = {
    status: {
        **response,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/APIErrorResponse"}}},
    }
    for status, response in API_ERROR_RESPONSES.items()
}


SSE_DOCUMENTATION_RESPONSES = {
    # FastAPI assigns additional response models the route's success media type.
    # Preserve JSON for HTTP errors before SSE opens; JSON routes register the model.
    **{
        status: {key: value for key, value in response.items() if key != "model"}
        for status, response in API_ERROR_RESPONSES.items()
    },
    200: {
        "model": SSEStreamSchema,
        "description": "Durable event stream. A replay is a snapshot ending with pending while the "
        "turn runs, or done when it has terminated. Persist accepted.turn_id (also supplied as "
        "X-Turn-ID when sending a turn) and the last durable event id to resume safely.",
        "content": {"text/event-stream": {}},
    },
}


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = phi2 - phi1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return round(6_371_000 * 2 * math.asin(min(1, math.sqrt(a))))
