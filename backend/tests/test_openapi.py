import json
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter

from app.domain.schemas import (
    APIErrorResponse,
    AppConfiguration,
    ConversationHistory,
    ConversationPage,
    ConversationSummary,
    ProfileResponse,
    SSEEvent,
    TurnRequest,
)


def test_published_openapi_snapshot_matches_running_api_contract(app):
    snapshot = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
    assert json.loads(snapshot.read_text()) == app.openapi()


def test_published_catalog_contract_has_typed_responses(app):
    schema = app.openapi()
    for path, method, expected in [
        ("/v1/search", "post", "ListingPage"),
        ("/v1/apartments/{apartment_id}", "get", "Listing"),
        ("/v1/apartments/{apartment_id}/verify", "post", "VerificationResponse"),
    ]:
        result = schema["paths"][path][method]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
        assert result["$ref"].endswith("/" + expected)
    assert "provenance" in schema["components"]["schemas"]["Listing"]["properties"]


def test_identity_and_conversation_responses_are_typed(app):
    schema = app.openapi()
    for path, method, status, expected in [
        ("/v1/me", "get", "200", "ProfileResponse"),
        ("/v1/conversations", "post", "201", "ConversationSummary"),
        ("/v1/conversations", "get", "200", "ConversationPage"),
        ("/v1/conversations/{conversation_id}", "get", "200", "ConversationHistory"),
        ("/v1/conversations/{conversation_id}/preferences", "put", "200", "Preferences"),
    ]:
        response = schema["paths"][path][method]["responses"][status]
        assert response["content"]["application/json"]["schema"]["$ref"].endswith("/" + expected)
    request = schema["components"]["schemas"]["ConversationRequest"]
    assert "client_conversation_id" not in request.get("required", [])
    assert {"type": "string", "format": "uuid"} in request["properties"]["client_conversation_id"][
        "anyOf"
    ]
    events = schema["components"]["schemas"]["HistoryTurn"]["properties"]["events"]["items"]
    assert events["discriminator"]["propertyName"] == "kind"
    assert set(events["discriminator"]["mapping"]) == {
        "message",
        "preferences",
        "done",
        "notice",
        "listings",
    }


def test_api_errors_document_actual_envelope_including_validation(app):
    schema = app.openapi()
    for path, path_item in schema["paths"].items():
        if not path.startswith("/v1/"):
            continue
        for operation in path_item.values():
            for status in ("413", "422", "429", "503"):
                response = operation["responses"][status]
                assert set(response["content"]) == {"application/json"}
                assert response["content"]["application/json"]["schema"]["$ref"].endswith(
                    "/APIErrorResponse"
                )
            retry_header = operation["responses"]["429"]["headers"]["Retry-After"]
            assert retry_header["schema"]["type"] == "integer"
    assert "detail" not in schema["components"]["schemas"]["APIErrorResponse"]["properties"]


def test_sse_contract_documents_text_wire_and_discriminated_payloads(app):
    schema = app.openapi()
    components = schema["components"]["schemas"]
    for path, method in [
        ("/v1/conversations/{conversation_id}/turns", "post"),
        ("/v1/conversations/{conversation_id}/turns/{turn_id}/events", "get"),
    ]:
        content = schema["paths"][path][method]["responses"]["200"]["content"]
        assert set(content) == {"text/event-stream"}
        wire_schema = content["text/event-stream"]["schema"]
        if "$ref" in wire_schema:
            wire_schema = components[wire_schema["$ref"].rsplit("/", 1)[-1]]
        assert wire_schema["type"] == "string"
        event_schema = wire_schema["x-event-schema"]
        assert event_schema["discriminator"]["propertyName"] == "event"
        assert set(event_schema["discriminator"]["mapping"]) == {
            "accepted",
            "status",
            "notice",
            "preferences",
            "listings",
            "message",
            "suggestions",
            "provider",
            "error",
            "done",
            "pending",
        }
        for event_ref in event_schema["oneOf"]:
            event = components[event_ref["$ref"].rsplit("/", 1)[-1]]
            assert "data" in event["required"]
            assert "$ref" in event["properties"]["data"]
    assert "id" not in components["SSEDoneEvent"]["required"]
    assert "id" not in components["SSEPendingEvent"]["required"]
    assert components["SSEPendingEvent"]["properties"]["id"]["type"] == "null"
    assert components["ProviderPayload"]["properties"]["status"]["type"] == "string"
    assert "enum" not in components["ProviderPayload"]["properties"]["status"]

    def assert_refs_resolve(value):
        if isinstance(value, dict):
            if "$ref" in value:
                reference = value["$ref"]
                assert reference.startswith("#/components/schemas/")
                assert reference.rsplit("/", 1)[-1] in components
            for nested in value.values():
                assert_refs_resolve(nested)
        elif isinstance(value, list):
            for nested in value:
                assert_refs_resolve(nested)

    assert_refs_resolve(schema)


def parsed_events(text):
    adapter = TypeAdapter(SSEEvent)
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        event = {"event": fields["event"], "data": json.loads(fields["data"])}
        if "id" in fields:
            event["id"] = int(fields["id"])
        yield adapter.validate_python(event)


async def test_real_conversation_and_sse_payloads_match_documented_models(client):
    ProfileResponse.model_validate((await client.get("/v1/me")).json())
    created = await client.post("/v1/conversations", json={"client_conversation_id": str(uuid4())})
    assert created.status_code == 201
    conversation = ConversationSummary.model_validate(created.json())
    page = ConversationPage.model_validate((await client.get("/v1/conversations")).json())
    assert page.items[0].id == conversation.id
    response = await client.post(
        f"/v1/conversations/{conversation.id}/turns",
        json={"client_turn_id": str(uuid4()), "message": "Хочу квартиру в Астане до 35 млн"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = list(parsed_events(response.text))
    assert events[0].event == "accepted" and events[-1].data.status == "complete"
    assert [e.id for e in events] == list(range(1, len(events) + 1))
    assert any(e.event == "listings" and e.data.items for e in events)
    assert any(e.event == "provider" for e in events)
    turn_id = events[0].data.turn_id
    assert response.headers["x-turn-id"] == str(turn_id)
    history = ConversationHistory.model_validate(
        (await client.get(f"/v1/conversations/{conversation.id}")).json()
    )
    assert history.turns[0].id == turn_id
    assert history.turns[0].status == "complete"
    assert sum(e.kind == "listings" for e in history.turns[0].events) == 1
    replay = await client.get(
        f"/v1/conversations/{conversation.id}/turns/{turn_id}/events", params={"after": events[-1].id}
    )
    terminal = list(parsed_events(replay.text))
    assert len(terminal) == 1 and terminal[0].event == "done"
    assert terminal[0].id is None


async def test_running_replay_marker_and_history_are_typed(app, client):
    user_id = (await client.get("/v1/me")).json()["id"]
    conversation_id = (await client.post("/v1/conversations", json={})).json()["id"]
    turn_id, _ = await app.state.chat.reserve(
        conversation_id, user_id, TurnRequest(client_turn_id=uuid4(), message="Хочу квартиру")
    )
    response = await client.get(f"/v1/conversations/{conversation_id}/turns/{turn_id}/events")
    events = list(parsed_events(response.text))
    assert len(events) == 1 and events[0].event == "pending"
    assert events[0].id is None and events[0].data.model_dump() == {}
    history = ConversationHistory.model_validate(
        (await client.get(f"/v1/conversations/{conversation_id}")).json()
    )
    assert history.turns[0].status == "running" and history.turns[0].events == []


async def test_actual_validation_error_matches_error_schema(client):
    response = await client.post("/v1/conversations", json={"client_conversation_id": "not-a-uuid"})
    assert response.status_code == 422
    assert APIErrorResponse.model_validate(response.json()).error.code == "invalid_request"
    assert "not-a-uuid" not in response.text


async def test_capability_discovery_supports_current_and_legacy_config(client):
    response = (await client.get("/v1/config")).json()
    current = AppConfiguration.model_validate(response)
    assert "idempotent_conversation_create" in current.capabilities
    legacy = AppConfiguration.model_validate(
        {key: value for key, value in response.items() if key != "capabilities"}
    )
    assert legacy.capabilities == []


async def test_failed_turn_stream_and_history_match_documented_models(app, client, monkeypatch):
    async def fail_plan(*args):
        raise RuntimeError("test planner unavailable")

    monkeypatch.setattr(app.state.assistant, "plan", fail_plan)
    conversation_id = (await client.post("/v1/conversations", json={})).json()["id"]
    response = await client.post(
        f"/v1/conversations/{conversation_id}/turns",
        json={"client_turn_id": str(uuid4()), "message": "Хочу квартиру"},
    )
    assert response.status_code == 200
    events = list(parsed_events(response.text))
    assert events[-2].event == "error" and events[-2].data.code == "turn_failed"
    assert events[-1].event == "done" and events[-1].data.status == "failed"
    history = ConversationHistory.model_validate(
        (await client.get(f"/v1/conversations/{conversation_id}")).json()
    )
    assert history.turns[0].status == "failed"
    assert history.turns[0].events[-1].payload.status == "failed"
