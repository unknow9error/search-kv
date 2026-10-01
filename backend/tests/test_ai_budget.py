from datetime import timedelta
from types import SimpleNamespace

from app.domain.models import KnowledgeChunk, utcnow
from app.domain.schemas import Preferences
from app.services.assistant import Decision, EvidenceSelection


class FakeModel:
    def __init__(self):
        self.calls = []
        self.responses = self

    async def parse(self, **kwargs):
        assert kwargs["store"] is False and kwargs["max_output_tokens"] <= 1400
        self.calls.append(kwargs)
        schema = kwargs["text_format"]
        if schema is Decision:
            output = Decision(
                action="knowledge",
                preferences=Preferences(city="Астана"),
                reason="none",
                knowledge_query="школа",
            )
        else:
            output = EvidenceSelection(evidence_ids=["school-fact", "invented-id"])
        return SimpleNamespace(output_parsed=output, usage=None)

    async def close(self):
        pass


async def test_rag_has_two_bounded_calls_and_drops_invented_citations(app):
    model = FakeModel()
    assistant = app.state.assistant
    assistant.client = model
    async with app.state.db.sessions.begin() as db:
        db.add(
            KnowledgeChunk(
                id="school-fact",
                title="Проверенный материал",
                text="Запланированная школа не означает действующую школу.",
                keywords="школа",
                source_url="https://example.com/school",
                reviewed_at=utcnow(),
                expires_at=utcnow() + timedelta(days=1),
                demo=False,
            )
        )
    decision, _ = await assistant.plan("test-user", "Что со школой?", Preferences(), False)
    answer = await assistant.knowledge("test-user", decision.knowledge_query)
    assert len(model.calls) == 2
    assert [x["id"] for x in answer["citations"]] == ["school-fact"]
    assert "invented-id" not in str(answer)


async def test_user_quota_prevents_second_model_call(app):
    assistant = app.state.assistant
    model = FakeModel()
    assistant.client = model
    assistant.settings.ai_daily_user_calls = 1
    await assistant.plan("limited-user", "Нужна светлая квартира у парка", Preferences(), False)
    await assistant.plan("limited-user", "Ещё запрос", Preferences(), False)
    assert len(model.calls) == 1


async def test_card_explanation_uses_no_model(app):
    model = FakeModel()
    app.state.assistant.client = model
    decision, _ = await app.state.assistant.plan(
        "user", "Почему мне подходит эта квартира?", Preferences(city="Астана"), True
    )
    assert decision.action == "explain" and model.calls == []


async def test_structured_filters_and_onboarding_do_not_use_model(app):
    model = FakeModel()
    app.state.assistant.client = model
    decision, _ = await app.state.assistant.plan("user", "Хочу квартиру", Preferences(), False)
    assert decision.action == "clarify" and decision.preferences.budget_max is None
    decision, _ = await app.state.assistant.plan(
        "user",
        "Покажи квартиры по моим условиям",
        Preferences(city="Астана", budget_max=35_000_000),
        False,
    )
    assert decision.action == "search" and model.calls == []
