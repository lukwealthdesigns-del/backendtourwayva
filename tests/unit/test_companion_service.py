"""
CompanionService (graph-backed turns), RevisionService (proposals) and the
confirmation of a proposed revision — with in-memory fakes, no database/network.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date, time
from types import SimpleNamespace as NS

import pytest

from app.core.constants import FeatureFlag, MessageRole, PendingChangeAction, PendingChangeStatus, TripItemType
from app.core.exceptions import ConflictError, ValidationAppError
from app.modules.companion import change_service as change_module
from app.modules.companion import service as companion_module
from app.modules.companion.change_service import PendingChangeService
from app.modules.companion.service import CompanionService
from app.modules.planning import revision_service as revision_module
from app.modules.planning.domain import GeoPoint
from app.modules.planning.revision_service import RevisionService, clean_instruction
from app.modules.planning.workflow import run_locally
from app.providers.llm.interface import LLMResponse
from app.providers.llm.mock_provider import MockLLMProvider


class _FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _reply(text, model="test-model"):
    return LLMResponse(content=text, model_used=model, prompt_tokens=10, completion_tokens=5, provider="mock")


async def _local_runner(spec, initial):
    return await run_locally(spec, initial)


# ---------------------------------------------------------------------------
# CompanionService
# ---------------------------------------------------------------------------
class _FakeConversations:
    def __init__(self):
        self.messages = []

    async def add_message(self, message):
        self.messages.append(message)
        return message

    async def list_messages(self, conversation_id, *, limit=50):
        return list(self.messages)[-limit:]

    async def list_all_messages(self, conversation_id):
        return list(self.messages)

    async def save_conversation(self, conversation):
        return conversation


class _FakeMemory:
    def __init__(self, facts=("likes museums",)):
        self.facts = facts
        self.loaded = 0

    async def list_enabled_for_user(self, user_id):
        self.loaded += 1
        return [NS(content=f) for f in self.facts]

    async def create_extracted(self, **kwargs):
        return None


@pytest.fixture()
def companion_env(monkeypatch):
    log = NS(features_checked=[], analytics=[], prefs_loaded=0, trip_context_calls=0)

    async def no_summary(conversation, messages, llm=None):
        return None

    async def no_memory(**kwargs):
        return None

    class _Entitlements:
        denied: set = set()

        def __init__(self, db):
            pass

        async def has_feature(self, user_id, flag):
            log.features_checked.append(flag)
            return flag not in _Entitlements.denied

    class _Prefs:
        def __init__(self, db):
            pass

        async def get(self, user_id):
            log.prefs_loaded += 1
            return NS(travel_styles=["adventure"], interests=[], budget_preference=None, accommodation_preference=None,
                      transportation_preference=None, walking_preference=None, dietary_preferences=[],
                      accessibility_preferences=[])

    monkeypatch.setattr(companion_module, "maybe_summarize", no_summary)
    monkeypatch.setattr(companion_module, "maybe_extract_memory", no_memory)
    monkeypatch.setattr(companion_module, "PreferencesRepository", _Prefs)
    monkeypatch.setattr("app.modules.entitlements.service.EntitlementService", _Entitlements)
    log.entitlements = _Entitlements
    _Entitlements.denied = set()
    return log


def _companion(llm, *, trip_id=None, facts=("likes museums",)):
    service = CompanionService.__new__(CompanionService)
    service.db, service.repo, service.memory_service = _FakeDB(), _FakeConversations(), _FakeMemory(facts)
    service.llm, service._runner = llm, _local_runner
    recorded = []

    async def record(user, state):
        recorded.append(state)

    service._record_turn = record
    service.recorded = recorded
    conversation = NS(id=uuid.uuid4(), user_id=uuid.uuid4(), trip_id=trip_id, summary=None)
    return service, conversation


def _user():
    return NS(id=uuid.uuid4(), language="en", currency="EUR")


def test_a_turn_routes_answers_persists_and_records_the_routing_decision(companion_env):
    llm = MockLLMProvider([_reply("It is 18°C and sunny.")])
    service, conversation = _companion(llm)

    message = asyncio.run(service.send_message(conversation=conversation, user=_user(),
                                               content="What's the weather like in Paris?"))

    assert message.role == MessageRole.ASSISTANT and message.content == "It is 18°C and sunny."
    assert message.model_used == "test-model"
    assert [m.role for m in service.repo.messages] == [MessageRole.USER, MessageRole.ASSISTANT]
    state = service.recorded[0]
    assert state["intent"].value == "WEATHER_QUERY" and state["intent_source"] == "rules"
    assert len(llm.calls) == 1                                       # rules classified it: no classifier call
    offered = {t["function"]["name"] for t in llm.calls[0]["tools"]}
    assert "get_weather" in offered and "propose_itinerary_revision" not in offered


def test_a_feature_outside_the_plan_gets_a_polite_refusal_and_no_model_call(companion_env):
    companion_env.entitlements.denied = {FeatureFlag.HOTELS}
    llm = MockLLMProvider([_reply("should not be used")])
    service, conversation = _companion(llm)

    message = asyncio.run(service.send_message(conversation=conversation, user=_user(), content="Find me a hotel in Rome"))

    assert "isn't included in your current plan" in message.content and message.model_used is None
    assert llm.calls == []
    assert service.recorded[0]["allowed"] is False


def test_only_relevant_context_is_loaded_for_each_intent(companion_env):
    service, conversation = _companion(MockLLMProvider([_reply("EUR 92")]))
    asyncio.run(service.send_message(conversation=conversation, user=_user(), content="Convert 100 USD to EUR"))
    assert service.memory_service.loaded == 0 and companion_env.prefs_loaded == 0        # a currency question needs neither

    service2, conversation2 = _companion(MockLLMProvider([_reply("Try Lisbon.")]))
    asyncio.run(service2.send_message(conversation=conversation2, user=_user(),
                                      content="Where can I go with a budget of 900 EUR for a week?"))
    assert service2.memory_service.loaded == 1 and companion_env.prefs_loaded == 1


def test_stored_facts_reach_the_prompt_only_when_the_intent_uses_them(companion_env):
    llm = MockLLMProvider([_reply("Try Lisbon.")])
    service, conversation = _companion(llm, facts=("prefers quiet places",))
    asyncio.run(service.send_message(conversation=conversation, user=_user(),
                                     content="Where can I go with a budget of 900 EUR for a week?"))
    system = llm.calls[0]["messages"][0]["content"]
    assert "prefers quiet places" in system and "travel styles: adventure" in system

    llm2 = MockLLMProvider([_reply("EUR 92")])
    service2, conversation2 = _companion(llm2, facts=("prefers quiet places",))
    asyncio.run(service2.send_message(conversation=conversation2, user=_user(), content="Convert 100 USD to EUR"))
    assert "prefers quiet places" not in llm2.calls[0]["messages"][0]["content"]


def test_trip_context_is_verified_and_failures_degrade_to_no_context(monkeypatch, companion_env):
    class _Trips:
        def __init__(self, db):
            pass

        async def get_trip_authorized(self, *, trip_id, user_id, require_editor=False):
            raise ForbiddenLike("no access")

    class ForbiddenLike(Exception):
        pass

    monkeypatch.setattr(companion_module, "TripService", _Trips)
    llm = MockLLMProvider([_reply("Nothing planned that I can see.")])
    service, conversation = _companion(llm, trip_id=uuid.uuid4())
    asyncio.run(service.send_message(conversation=conversation, user=_user(), content="What's planned for day 2?"))
    assert "Current trip" not in llm.calls[0]["messages"][0]["content"]          # removed from the trip => no leak


def test_trip_context_is_injected_for_trip_questions(monkeypatch, companion_env):
    trip = NS(id=uuid.uuid4(), destination="Paris", start_date=date(2026, 10, 1), end_date=date(2026, 10, 1),
              travelers=1, budget_amount=None, budget_currency=None)
    day = NS(id=uuid.uuid4(), day_number=1, date=date(2026, 10, 1), weather_summary=None)
    item = NS(id="item-1", title="Louvre", item_type=TripItemType.ACTIVITY, start_time=time(9), end_time=time(11),
              estimated_cost=None, currency=None)

    class _Trips:
        def __init__(self, db):
            pass

        async def get_trip_authorized(self, *, trip_id, user_id, require_editor=False):
            return trip

    class _Repo:
        def __init__(self, db):
            pass

        async def list_days_for_trip(self, trip_id):
            return [day]

        async def list_items_for_day(self, day_id):
            return [item]

    monkeypatch.setattr(companion_module, "TripService", _Trips)
    monkeypatch.setattr(companion_module, "TripRepository", _Repo)
    llm = MockLLMProvider([_reply("The Louvre at 9.")])
    service, conversation = _companion(llm, trip_id=trip.id)
    asyncio.run(service.send_message(conversation=conversation, user=_user(), content="What's planned for day 1?"))
    system = llm.calls[0]["messages"][0]["content"]
    assert "Current trip" in system and "Louvre" in system and "[item item-1]" in system


def test_post_turn_failures_never_fail_a_saved_reply(monkeypatch, companion_env):
    async def boom(*a, **k):
        raise RuntimeError("summarizer down")

    monkeypatch.setattr(companion_module, "maybe_summarize", boom)
    monkeypatch.setattr(companion_module, "maybe_extract_memory", boom)
    service, conversation = _companion(MockLLMProvider([_reply("Hi there!")]))
    message = asyncio.run(service.send_message(conversation=conversation, user=_user(), content="hello"))
    assert message.content and service.repo.messages[-1] is message


# ---------------------------------------------------------------------------
# RevisionService
# ---------------------------------------------------------------------------
def _db_item(title, kind=TripItemType.ACTIVITY, **over):
    base = dict(id=uuid.uuid4(), item_type=kind, title=title, description=None, location_name=f"{title}, Paris",
                latitude=48.86, longitude=2.35, start_time=time(10), end_time=time(11), estimated_cost=30.0,
                currency="EUR", provider="ai", source="estimated", external_id=None, booking_link=None,
                image_url=None, notes=None)
    base.update(over)
    return NS(**base)


class _RevisionEnv:
    def __init__(self, monkeypatch, *, items_by_day=None, replies=()):
        self.trip = NS(id=uuid.uuid4(), destination="Paris", origin=None, start_date=date(2026, 10, 1),
                       end_date=date(2026, 10, 2), travelers=2, budget_amount=2000.0, budget_currency="EUR",
                       overview="ov", current_version_number=4)
        self.days = [NS(id=uuid.uuid4(), day_number=1, date=date(2026, 10, 1), weather_summary=None),
                     NS(id=uuid.uuid4(), day_number=2, date=date(2026, 10, 2), weather_summary=None)]
        louvre = _db_item("Louvre", start_time=time(9), end_time=time(11))
        orsay = _db_item("Orsay", start_time=time(13), end_time=time(15))
        bistro = _db_item("Bistro", start_time=time(19), end_time=time(21))
        walk = _db_item("Walk", start_time=time(10), end_time=time(11))
        self.items = items_by_day if items_by_day is not None else {
            self.days[0].id: [louvre, orsay], self.days[1].id: [walk, bistro]}
        self.pending = []
        self.llm_replies = list(replies)
        env = self

        class _Trips:
            def __init__(self, db):
                pass

            async def get_trip_authorized(self, *, trip_id, user_id, require_editor=False):
                return env.trip

        class _TripRepo:
            def __init__(self, db):
                pass

            async def list_days_for_trip(self, trip_id):
                return env.days

            async def list_items_for_day(self, day_id):
                return env.items.get(day_id, [])

        class _Pending:
            def __init__(self, db):
                pass

            async def create(self, change):
                change.id = uuid.uuid4()
                env.pending.append(change)
                return change

        def build_ports(db, *, user_id, trip_id, llm=None, premium_ai=False):
            env.ports_premium = premium_ai

            async def geocode(query):
                return GeoPoint(latitude=48.86, longitude=2.35, formatted_address=query)

            async def llm_call(messages, temperature, max_tokens):
                return env.llm_replies.pop(0) if len(env.llm_replies) > 1 else env.llm_replies[0]

            return NS(geocode=geocode, llm=llm_call)

        class _Entitlements:
            def __init__(self, db):
                pass

            async def has_feature(self, user_id, flag):
                env.premium_checked = flag
                return env.premium_ai

        self.premium_ai = True
        self.premium_checked = None
        monkeypatch.setattr(revision_module, "EntitlementService", _Entitlements)
        monkeypatch.setattr(revision_module, "TripService", _Trips)
        monkeypatch.setattr(revision_module, "TripRepository", _TripRepo)
        monkeypatch.setattr(revision_module, "PendingChangeRepository", _Pending)
        monkeypatch.setattr(revision_module, "build_ports", build_ports)

    def service(self):
        return RevisionService(_FakeDB(), runner=_local_runner)

    def plan_reply(self, mutate=lambda d: None):
        data = {"overview": "Revised.", "days": []}
        for day in self.days:
            data["days"].append({"day_number": day.day_number, "items": [
                {"item_type": i.item_type.value, "title": i.title, "location_name": i.location_name,
                 "start_time": i.start_time.strftime("%H:%M"), "end_time": i.end_time.strftime("%H:%M"),
                 "estimated_cost": i.estimated_cost} for i in self.items[day.id]]})
        mutate(data)
        return json.dumps(data)


def _propose(env, instruction="make it cheaper"):
    return asyncio.run(env.service().propose(conversation_id=uuid.uuid4(), trip_id=env.trip.id, user=_user(),
                                             instruction=instruction))


def test_a_valid_revision_becomes_a_pending_proposal_with_the_full_plan(monkeypatch):
    env = _RevisionEnv(monkeypatch)
    env.llm_replies = [env.plan_reply(lambda d: d["days"][1]["items"].pop())]        # drop the bistro (last item of day 2)

    result = _propose(env)

    assert result["changed"] is True and "Bistro" in result["summary"]
    (change,) = env.pending
    assert change.action == PendingChangeAction.REVISE_TRIP and change.status == PendingChangeStatus.PENDING
    assert change.day_id is None and change.item_id is None
    assert change.payload["base_version"] == 4 and change.payload["instruction"] == "make it cheaper"
    assert "Bistro" not in json.dumps(change.payload["plan"])
    assert result["estimated_cost_after"] < result["estimated_cost_before"]


def test_a_request_that_changes_nothing_creates_no_proposal(monkeypatch):
    env = _RevisionEnv(monkeypatch)
    env.llm_replies = [env.plan_reply()]
    result = _propose(env, "keep everything the same")
    assert result["changed"] is False and env.pending == []


def test_revising_a_trip_with_no_itinerary_is_refused(monkeypatch):
    env = _RevisionEnv(monkeypatch, items_by_day={})
    with pytest.raises(ValidationAppError, match="no itinerary"):
        _propose(env)


def test_an_unusable_model_answer_is_reported_and_nothing_is_stored(monkeypatch):
    env = _RevisionEnv(monkeypatch, replies=["not json"])
    with pytest.raises(ValidationAppError):
        _propose(env)
    assert env.pending == []


def test_instructions_are_cleaned_and_bounded():
    assert clean_instruction("  make   it \n cheaper ") == "make it cheaper"
    assert len(clean_instruction("x" * 2000)) == 500
    for empty in ("", "   ", None):
        with pytest.raises(ValidationAppError):
            clean_instruction(empty)


# ---------------------------------------------------------------------------
# Confirming a proposed revision
# ---------------------------------------------------------------------------
class _ConfirmEnv:
    def __init__(self, monkeypatch, *, trip_version=4, payload=None):
        self.trip = NS(id=uuid.uuid4(), current_version_number=trip_version)
        plan = {"overview": "Revised", "currency": "EUR", "days": [{"day_number": 1, "date": "2026-10-01", "items": [
            {"item_type": "activity", "title": "Louvre", "estimated_cost": 30.0, "currency": "EUR"}]}]}
        self.change = NS(id=uuid.uuid4(), trip_id=self.trip.id, action=PendingChangeAction.REVISE_TRIP,
                         status=PendingChangeStatus.PENDING, summary="Revise trip: make it cheaper",
                         payload=payload if payload is not None else {"base_version": 4, "plan": plan},
                         decided_by=None, proposed_by=uuid.uuid4())
        env = self
        self.persisted = []

        class _Persistence:
            def __init__(self, db, *, trip_id, actor_id, commit=True):
                env.persist_args = {"trip_id": trip_id, "actor_id": actor_id, "commit": commit}

            async def __call__(self, plan, meta):
                env.persisted.append((plan, meta))
                return {"version_number": 5, "items": 1}

        monkeypatch.setattr(change_module, "PlanningPersistence", _Persistence)

        service = PendingChangeService.__new__(PendingChangeService)
        service.db = _FakeDB()

        class _Repo:
            async def save(self, change):
                return change

        class _Trips:
            async def get_trip_authorized(self, **kwargs):
                return env.trip

        class _TripRepo:
            async def get_trip(self, trip_id):
                return env.trip

        service.repo, service.trip_service, service.trip_repo = _Repo(), _Trips(), _TripRepo()

        async def get_change(change_id):
            return env.change

        async def notify(change, *, decision):
            return None

        service.get_change, service._notify_decision = get_change, notify
        self.service = service

    def confirm(self):
        return asyncio.run(self.service.confirm(change_id=self.change.id, actor_id=uuid.uuid4()))


def test_confirming_a_current_revision_applies_it_in_the_same_transaction(monkeypatch):
    env = _ConfirmEnv(monkeypatch)
    change, result = env.confirm()
    assert result == {"version_number": 5, "items": 1, "applied": True}
    assert env.persist_args["commit"] is False                    # folded into ONE commit with the status change
    assert env.service.db.commits == 1 and change.status == PendingChangeStatus.CONFIRMED
    plan, meta = env.persisted[0]
    assert plan.days[0].items[0].title == "Louvre" and meta["change_summary"] == "Revise trip: make it cheaper"


def test_a_stale_revision_is_refused_so_newer_edits_are_not_overwritten(monkeypatch):
    env = _ConfirmEnv(monkeypatch, trip_version=7)               # trip moved on since the proposal (v4)
    with pytest.raises(ConflictError, match="changed after"):
        env.confirm()
    assert env.persisted == [] and env.change.status == PendingChangeStatus.PENDING and env.service.db.commits == 0


def test_a_malformed_stored_proposal_is_rejected_cleanly(monkeypatch):
    env = _ConfirmEnv(monkeypatch, payload={"base_version": 4, "plan": {"days": "nope"}})
    with pytest.raises(ValidationAppError, match="malformed"):
        env.confirm()
    assert env.persisted == []


def test_the_strong_model_tier_is_used_for_revisions_only_with_the_premium_ai_feature(monkeypatch):
    env = _RevisionEnv(monkeypatch)
    env.llm_replies = [env.plan_reply()]
    _propose(env)
    assert env.premium_checked.value == "PREMIUM_AI" and env.ports_premium is True

    plain = _RevisionEnv(monkeypatch)
    plain.premium_ai = False
    plain.llm_replies = [plain.plan_reply()]
    _propose(plain)
    assert plain.ports_premium is False


def test_without_the_memory_feature_nothing_is_remembered_or_recalled(monkeypatch, companion_env):
    companion_env.entitlements.denied = {FeatureFlag.MEMORY}
    extracted = []

    async def spy_extract(**kwargs):
        extracted.append(kwargs)
        return None

    monkeypatch.setattr(companion_module, "maybe_extract_memory", spy_extract)
    llm = MockLLMProvider([_reply("Try Lisbon.")])
    service, conversation = _companion(llm, facts=("prefers quiet places",))
    asyncio.run(service.send_message(conversation=conversation, user=_user(),
                                     content="Where can I go with a budget of 900 EUR for a week?"))
    system = llm.calls[0]["messages"][0]["content"]
    assert "prefers quiet places" not in system and service.memory_service.loaded == 0      # not recalled
    assert extracted == []                                                                  # not remembered
    assert "travel styles: adventure" in system                                             # profile preferences are unaffected

    companion_env.entitlements.denied = set()
    llm2 = MockLLMProvider([_reply("Try Lisbon.")])
    service2, conversation2 = _companion(llm2)
    asyncio.run(service2.send_message(conversation=conversation2, user=_user(),
                                      content="Where can I go with a budget of 900 EUR for a week?"))
    assert len(extracted) == 1
