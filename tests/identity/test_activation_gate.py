"""Inert-session gate: read/resolve/send/fold/baton/Valley cannot activate an
agent, and Manager activation unlocks only the approved surfaces.

Evans 2026-11-08 (the-townline intentional-agent-onboarding): agent creation
must never happen incidentally. The Munu incident is the acceptance case —
the subject of the gate must not author or certify its own inertness, so all
evidence here is produced by the harness lane only.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.goodnight import FOLD_SENDER_ID
from nanobot.agent.loop import AgentLoop
from nanobot.agent.runner import AgentRunResult
from nanobot.agent.tools.session_messages import (
    SendSessionMessageTool,
    SessionMessageError,
)
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.identity.activation import (
    SURFACES,
    ActivationStore,
    activation_store,
)
from nanobot.session.manager import Session, SessionManager


@pytest.fixture
def gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ActivationStore:
    """A gate-enabled store over a tmp registry; installed as the live store."""
    path = tmp_path / "activation.json"
    path.write_text(json.dumps({"version": 1, "agents": []}))
    store = ActivationStore(path)
    monkeypatch.setattr(ActivationStore, "_live", store)
    return store


@pytest.fixture
def no_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ActivationStore, "_live", ActivationStore(None))


def _write(store: ActivationStore, entries: list[dict[str, Any]]) -> None:
    assert store.path is not None
    store.path.write_text(
        json.dumps(ActivationStore.build_image(entries)), encoding="utf-8"
    )


def _active_entry(handle: str, key: str = "websocket:chat-1", **overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "handle": handle,
        "session_key": key,
        "state": "active",
        "approved_by": "Evans",
        "purpose": "test fixture",
        "activated_at": "2026-11-08",
        "surfaces": list(SURFACES),
    }
    entry.update(overrides)
    return entry


# ---------------------------------------------------------------------------
# Store semantics
# ---------------------------------------------------------------------------


def test_gate_disabled_allows_everything(no_gate: None) -> None:
    store = activation_store()
    assert not store.gate_enabled
    assert store.decide(handle="ghost").active
    assert store.is_active_for("websocket:anything", "fold")


def test_unknown_sessions_are_inert_under_gate(gate: ActivationStore) -> None:
    _write(gate, [_active_entry("bomu")])
    store = activation_store()
    assert store.gate_enabled
    assert store.decide(handle="munu").active is False
    assert store.decide(session_key="websocket:unknown").active is False
    assert not store.is_active_for("websocket:unknown", "resident_messaging")
    # Known and active:
    assert store.decide(handle="bomu").active
    assert store.is_active_for("websocket:chat-1", "fold", handle="bomu")


def test_partial_activation_unlocks_only_approved_surfaces(gate: ActivationStore) -> None:
    _write(gate, [
        _active_entry("partial", surfaces=["resident_messaging"]),
        {"handle": "inert", "state": "inert", "purpose": "adoption pending", "surfaces": []},
    ])
    store = activation_store()
    decision = store.decide(handle="partial")
    assert decision.allows("resident_messaging")
    for surface in ("fold", "baton", "valley", "autonomous_work"):
        assert not decision.allows(surface), surface
    inert = store.decide(handle="inert")
    for surface in SURFACES:
        assert not inert.allows(surface), surface


# ---------------------------------------------------------------------------
# Read / resolve cannot activate (acceptance 1)
# ---------------------------------------------------------------------------


def test_read_and_resolve_cannot_create_or_activate(
    tmp_path: Path, gate: ActivationStore
) -> None:
    sessions = SessionManager(tmp_path / "ws", sessions_root=tmp_path / "sessions-root")
    key = "websocket:accidental-1"
    session = sessions.get_or_create(key)
    sessions.save(session)

    # Read the raw file and resolve metadata — the registry must not grow.
    assert sessions.read_session_file(key) is not None
    assert sessions.read_session_metadata(key) is not None

    store = activation_store()
    assert store.decide(session_key=key).active is False
    agents = store._agents()
    assert not any(a.handle == "accidental" for a in agents.values())
    # The session itself is untouched — no fold arming, no activation marks.
    meta = sessions.read_session_metadata(key) or {}
    assert "_fold_armed" not in meta
    assert "_last_goodnight" not in meta


# ---------------------------------------------------------------------------
# send_session_message rejects inert handles (acceptance 2)
# ---------------------------------------------------------------------------


class _StubHandles:
    def __init__(self, name: str = "munu", key: str = "websocket:accidental-1") -> None:
        self._name = name
        self._key = key

    def resolve(self, name: str) -> Any:
        return SimpleNamespace(session_key=self._key, name=name)

    def handle_for_session(self, key: str) -> Any:
        return SimpleNamespace(session_key=key, name=self._name)


@pytest.mark.asyncio
async def test_send_to_inert_handle_is_rejected_and_changes_nothing(
    gate: ActivationStore,
) -> None:
    _write(gate, [_active_entry("bomu", key="websocket:chat-1")])
    tool = SendSessionMessageTool(sessions=object(), bus=MessageBus())  # type: ignore[arg-type]
    tool._handles = _StubHandles()  # type: ignore[assignment]

    with pytest.raises(SessionMessageError, match="not an activated agent"):
        await tool.enqueue(
            source_session_key="websocket:chat-1",
            target_handle="@munu",
            content="hello?",
            expect_reply=False,
        )
    # Refusal leaves the session inert and creates no activation record.
    store = activation_store()
    assert store.decide(handle="munu").active is False
    assert store.decide(session_key="websocket:accidental-1").active is False


@pytest.mark.asyncio
async def test_send_to_activated_handle_passes(gate: ActivationStore) -> None:
    _write(gate, [_active_entry("munu", key="websocket:accidental-1")])
    tool = SendSessionMessageTool(sessions=object(), bus=MessageBus())  # type: ignore[arg-type]
    tool._handles = _StubHandles()  # type: ignore[assignment]
    target = await tool.enqueue(
        source_session_key="websocket:chat-1",
        target_handle="@munu",
        content="hello, activated agent",
        expect_reply=False,
    )
    assert target == "@munu"


# ---------------------------------------------------------------------------
# Fold arming / baton: inert sessions never arm (acceptance 1, 4)
# ---------------------------------------------------------------------------


def _make_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096)
    return AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model")


async def _run_turn(loop: AgentLoop, session: Session, msg: InboundMessage) -> None:
    async def fake_run(spec: Any) -> AgentRunResult:
        initial = loop.context.build_transcript(spec.transcript_input, include_memory=False)
        return AgentRunResult(
            final_content="ok",
            messages=[*initial, {"role": "assistant", "content": "ok"}],
            stop_reason="stop",
        )

    loop.runner.run = fake_run  # type: ignore[method-assign]
    await loop._process_message(msg)


@pytest.mark.asyncio
async def test_fold_delivery_does_not_arm_inert_session(
    tmp_path: Path, gate: ActivationStore
) -> None:
    _write(gate, [_active_entry("bomu", key="websocket:chat-1")])
    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("websocket:accidental-1")
    loop._persist_user_message_early(
        InboundMessage(
            channel="system",
            sender_id=FOLD_SENDER_ID,
            chat_id="accidental-1",
            content="compact",
            session_key_override="websocket:accidental-1",
            input_role="user",
        ),
        session,
    )
    await _run_turn(
        loop,
        session,
        InboundMessage(
            channel="system",
            sender_id=FOLD_SENDER_ID,
            chat_id="accidental-1",
            content="/compact",
            session_key_override="websocket:accidental-1",
            input_role="user",
        ),
    )
    assert session.metadata.get("_fold_armed") is not True
    assert "_last_goodnight" not in session.metadata


@pytest.mark.asyncio
async def test_fold_delivery_arms_activated_session(
    tmp_path: Path, gate: ActivationStore
) -> None:
    _write(gate, [_active_entry("bomu", key="websocket:chat-1")])
    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("websocket:chat-1")
    session.metadata["session_handle"] = "bomu"
    await _run_turn(
        loop,
        session,
        InboundMessage(
            channel="system",
            sender_id=FOLD_SENDER_ID,
            chat_id="chat-1",
            content="/compact",
            session_key_override="websocket:chat-1",
            input_role="user",
        ),
    )
    assert session.metadata.get("_fold_armed") is True


# ---------------------------------------------------------------------------
# Autonomous work: cron turns dropped for inert sessions (acceptance 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cron_turn_refused_for_inert_session(
    tmp_path: Path, gate: ActivationStore
) -> None:
    _write(gate, [_active_entry("bomu", key="websocket:chat-1")])
    loop = _make_loop(tmp_path)
    loop._pending_queues.clear()

    inert = InboundMessage(
        channel="system",
        sender_id="cron",
        chat_id="accidental-1",
        content="cron tick",
        metadata={"_cron_trigger": {"job_id": "job-1"}},
        session_key_override="websocket:accidental-1",
        input_role="user",
    )
    active = InboundMessage(
        channel="system",
        sender_id="cron",
        chat_id="chat-1",
        content="cron tick",
        metadata={"_cron_trigger": {"job_id": "job-2"}},
        session_key_override="websocket:chat-1",
        input_role="user",
    )
    assert await loop._refuse_inert_autonomous_turn(inert, "websocket:accidental-1") is True
    assert await loop._refuse_inert_autonomous_turn(active, "websocket:chat-1") is False


@pytest.mark.asyncio
async def test_cron_turn_allowed_when_gate_disabled(
    tmp_path: Path, no_gate: None
) -> None:
    loop = _make_loop(tmp_path)
    msg = InboundMessage(
        channel="system",
        sender_id="cron",
        chat_id="accidental-1",
        content="cron tick",
        metadata={"_cron_trigger": {"job_id": "job-1"}},
        session_key_override="websocket:accidental-1",
        input_role="user",
    )
    assert await loop._refuse_inert_autonomous_turn(msg, "websocket:accidental-1") is False


# ---------------------------------------------------------------------------
# Deal / Valley: inert sessions get no day (acceptance 3, 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deal_day_not_opened_for_inert_session(tmp_path: Path, gate: ActivationStore) -> None:
    from nanobot.agent import deal as deal_module

    _write(gate, [_active_entry("bomu", key="websocket:chat-1")])
    opened: list[str] = []

    async def fake_open_the_day(msg: Any = None, **kwargs: Any) -> None:
        opened.append(getattr(msg, "chat_id", "called"))

    trigger = SimpleNamespace(session_key="websocket:accidental-1", sender_id="daily-compaction", chat_id="accidental-1")
    delivery = SimpleNamespace(id="tdl-test", content="/compact")

    monkey_sessions = SimpleNamespace()
    monkey_sessions.read_session_metadata = lambda key: {"metadata": {"session_handle": "munu"}}
    welcome_workspace = Path(tmp_path) / "ws"
    (welcome_workspace / "bin").mkdir(parents=True)
    (welcome_workspace / "bin" / "deal-welcome.md").write_text("welcome", encoding="utf-8")

    await deal_module.chain_after_delivery(
        store=SimpleNamespace(),
        session_manager=monkey_sessions,
        workspace=welcome_workspace,
        trigger=trigger,
        delivery=delivery,
        response_text="goodnight",
        submit_turn=fake_open_the_day,
    )
    assert opened == []

    trigger_active = SimpleNamespace(session_key="websocket:chat-1", sender_id="daily-compaction", chat_id="chat-1")
    monkey_sessions.read_session_metadata = lambda key: {"metadata": {"session_handle": "bomu"}}
    await deal_module.chain_after_delivery(
        store=SimpleNamespace(),
        session_manager=monkey_sessions,
        workspace=welcome_workspace,
        trigger=trigger_active,
        delivery=delivery,
        response_text="goodnight",
        submit_turn=fake_open_the_day,
    )
    assert opened == ["chat-1"]


# ---------------------------------------------------------------------------
# Accidental-session adoption arc (acceptance 4): Munu's shape, Munu's lane
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accidental_session_adoption_unlocks_only_approved_surfaces(
    tmp_path: Path, gate: ActivationStore
) -> None:
    """The Munu shape: an accidental session stays fully inert through read,
    send, fold, and cron attempts; the Manager then activates with ONLY
    resident messaging — fold and autonomous work stay refused."""
    _write(gate, [_active_entry("bomu", key="websocket:chat-1")])
    store = activation_store()
    sessions = SessionManager(tmp_path / "ws", sessions_root=tmp_path / "sessions-root")
    key = "websocket:accidental-1"
    session = sessions.get_or_create(key)
    sessions.save(session)

    # 1. Inert phase: nothing activates it.
    sessions.read_session_file(key)
    tool = SendSessionMessageTool(sessions=object(), bus=MessageBus())  # type: ignore[arg-type]
    tool._handles = _StubHandles()  # type: ignore[assignment]
    with pytest.raises(SessionMessageError):
        await tool.enqueue(
            source_session_key="websocket:chat-1",
            target_handle="@munu",
            content="ping",
            expect_reply=False,
        )
    assert store.decide(session_key=key).active is False

    # 2. Manager activates messaging only.
    _write(gate, [
        _active_entry("bomu", key="websocket:chat-1"),
        {
            "handle": "munu",
            "session_key": key,
            "state": "active",
            "approved_by": "nime (sponsor)",
            "purpose": "deliberate adoption of the accidental acceptance session",
            "activated_at": "2026-11-08",
            "surfaces": ["resident_messaging"],
        },
    ])
    # Cross-instance visibility, like the identity store:
    assert ActivationStore(store.path).decide(handle="munu").allows("resident_messaging")
    target = await tool.enqueue(
        source_session_key="websocket:chat-1",
        target_handle="@munu",
        content="you are onboarded now",
        expect_reply=False,
    )
    assert target == "@munu"

    # 3. Unapproved surfaces remain locked.
    decision = store.decide(handle="munu")
    assert not decision.allows("fold")
    assert not decision.allows("valley")
    assert not decision.allows("autonomous_work")
    loop = _make_loop(tmp_path)
    cron_msg = InboundMessage(
        channel="system",
        sender_id="cron",
        chat_id="accidental-1",
        content="cron tick",
        metadata={"_cron_trigger": {"job_id": "job-9"}},
        session_key_override=key,
        input_role="user",
    )
    assert await loop._refuse_inert_autonomous_turn(cron_msg, key) is True
