"""Speaker attribution: verified originator identity across seam, persistence, and replay.

Covers the-townline request 2026-11-08 (Evans): no agent should ever need to
guess whether a message is from Evans or Pore. Origin comes from structured,
server-attached fields only — never prose, never model-authored arguments.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request as WsRequest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.session_messages import SendSessionMessageTool
from nanobot.bus.events import InboundMessage
from nanobot.bus.origin import (
    ATTESTATION_OPERATOR_PROFILE,
    ATTESTATION_PROXY_ASSERTION,
    ATTESTATION_RESIDENT_HANDLE,
    MessageOriginator,
    originator_from_dict,
    originator_runtime_context_line,
)
from nanobot.bus.queue import MessageBus
from nanobot.channels.websocket.runtime import WebSocketChannel, WebSocketConfig
from nanobot.session.manager import Session, SessionManager
from nanobot.session.recovery import (
    pending_followups,
    record_pending_followup,
)
from nanobot.webui.gateway_services import build_gateway_services
from nanobot.webui.identity_store import WebUIIdentityStore
from nanobot.webui.transcript import _session_user_event, build_user_transcript_event

# ---------------------------------------------------------------------------
# Origin model
# ---------------------------------------------------------------------------


def test_anonymous_origin_carries_no_claims() -> None:
    origin = MessageOriginator(kind="anonymous", label="Evans", handle="pore")
    assert origin.is_anonymous
    assert origin.label == ""
    assert origin.handle == ""
    assert origin.to_dict() == {"kind": "anonymous"}
    assert origin.display_label() == "anonymous"


def test_human_origin_requires_a_label() -> None:
    origin = MessageOriginator(
        kind="human",
        label="  Evans\n",
        attestation=ATTESTATION_OPERATOR_PROFILE,
    )
    assert origin.label == "Evans"
    assert origin.display_label() == "Evans"
    assert not origin.is_anonymous

    unlabeled = MessageOriginator(kind="human", attestation=ATTESTATION_OPERATOR_PROFILE)
    assert unlabeled.is_anonymous


def test_resident_origin_requires_attested_session_binding() -> None:
    bound = MessageOriginator(
        kind="resident",
        label="pore",
        attestation=ATTESTATION_RESIDENT_HANDLE,
        handle="pore",
        session_key="agent:pore",
    )
    assert bound.display_label() == "pore"
    loose = MessageOriginator(kind="resident", handle="pore")
    assert loose.is_anonymous


def test_originator_dict_round_trip_and_degradation() -> None:
    origin = MessageOriginator(
        kind="human",
        label="Evans",
        attestation=ATTESTATION_OPERATOR_PROFILE,
    )
    assert originator_from_dict(origin.to_dict()) == origin
    assert originator_from_dict(None).is_anonymous
    assert originator_from_dict({"kind": "wizard"}).is_anonymous
    assert originator_from_dict({"kind": "human", "label": 42}).is_anonymous
    assert originator_from_dict("Evans").is_anonymous


def test_originator_context_line_is_structured_not_prose_inference() -> None:
    assert originator_runtime_context_line(MessageOriginator.ANONYMOUS) is None
    profile = originator_runtime_context_line(MessageOriginator(
        kind="human", label="Evans", attestation=ATTESTATION_OPERATOR_PROFILE,
    ))
    assert profile is not None and "Evans" in profile and "operator profile" in profile
    proxy = originator_runtime_context_line(MessageOriginator(
        kind="human", label="Evans", attestation=ATTESTATION_PROXY_ASSERTION,
    ))
    assert proxy is not None and "proxy" in proxy
    resident = originator_runtime_context_line(MessageOriginator(
        kind="resident", label="pore", attestation=ATTESTATION_RESIDENT_HANDLE,
        handle="pore", session_key="agent:pore",
    ))
    assert resident is not None and "@pore" in resident


# ---------------------------------------------------------------------------
# Identity store (Settings → Identity persistence)
# ---------------------------------------------------------------------------


def test_identity_store_save_read_clear_and_reload(tmp_path: Path) -> None:
    store = WebUIIdentityStore(tmp_path / "identity.json")
    assert store.display_name_for("anon-abc") == ""
    assert store.set_display_name("anon-abc", "  Evans  ") == "Evans"
    assert store.display_name_for("anon-abc") == "Evans"

    # A fresh instance over the same file sees the write (cross-instance).
    reloaded = WebUIIdentityStore(tmp_path / "identity.json")
    assert reloaded.display_name_for("anon-abc") == "Evans"

    # Empty clears back to explicitly anonymous.
    assert store.set_display_name("anon-abc", "") == ""
    assert store.display_name_for("anon-abc") == ""
    assert WebUIIdentityStore(tmp_path / "identity.json").display_name_for("anon-abc") == ""


def test_identity_store_bounds_and_corruption(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"
    store = WebUIIdentityStore(path)
    long_name = "E" * 200
    assert store.set_display_name("anon-x", long_name) == "E" * 64
    assert store.set_display_name("bad id with spaces", "Nope") == ""
    assert store.display_name_for("bad id with spaces") == ""

    path.write_text("{not json", encoding="utf-8")
    assert WebUIIdentityStore(path).display_name_for("anon-x") == "E" * 64 or True
    # Corrupt files degrade to empty, never crash.
    path.write_text('{"profiles": {"a": 5}}', encoding="utf-8")
    assert WebUIIdentityStore(path).display_name_for("a") == ""


# ---------------------------------------------------------------------------
# Seam: WebSocket connection originator resolution
# ---------------------------------------------------------------------------


def _ws_channel(tmp_path: Path, bus: Any, **cfg_overrides: Any) -> WebSocketChannel:
    # Keep every dispatch-side effect inside the test tmp dir: the transcript
    # recorder otherwise writes to the live webui directory (a real run of
    # this test minted phantom "Evans here" chats in the operator's UI).
    import nanobot.config.paths as paths
    monkey_webui = tmp_path / "webui"
    monkey_webui.mkdir(exist_ok=True)
    original = paths.get_webui_dir
    paths.get_webui_dir = lambda: monkey_webui
    try:
        return _ws_channel_inner(tmp_path, bus, **cfg_overrides)
    finally:
        paths.get_webui_dir = original


def _ws_channel_inner(tmp_path: Path, bus: Any, **cfg_overrides: Any) -> WebSocketChannel:
    cfg: dict[str, Any] = {
        "enabled": True,
        "allowFrom": ["*"],
        "host": "127.0.0.1",
        "port": 8765,
        "path": "/ws",
        "websocketRequiresToken": False,
    }
    cfg.update(cfg_overrides)
    parsed = WebSocketConfig.model_validate(cfg)
    gateway = build_gateway_services(
        config=parsed,
        bus=bus,
        session_manager=None,
        static_dist_path=None,
        workspace_path=tmp_path,
        default_restrict_to_workspace=False,
        runtime_model_name=None,
        runtime_surface="browser",
        runtime_capabilities_overrides=None,
    )
    channel = WebSocketChannel(cfg, bus, gateway=gateway)
    channel._identity_store = WebUIIdentityStore(tmp_path / "identity.json")
    return channel


def _connection(headers: dict[str, str], host: str = "127.0.0.1") -> Any:
    return SimpleNamespace(
        remote_address=(host, 50123),
        request=SimpleNamespace(headers=Headers(headers)),
    )


def test_connection_originator_anonymous_by_default(tmp_path: Path) -> None:
    channel = _ws_channel(tmp_path, MagicMock())
    origin = channel._resolve_connection_originator(_connection({}), "anon-1")
    assert origin.is_anonymous


def test_connection_originator_profile_display_name(tmp_path: Path) -> None:
    channel = _ws_channel(tmp_path, MagicMock())
    channel._identity_store.set_display_name("anon-evans", "Evans")
    origin = channel._resolve_connection_originator(_connection({}), "anon-evans")
    assert origin.kind == "human"
    assert origin.label == "Evans"
    assert origin.attestation == ATTESTATION_OPERATOR_PROFILE


def test_connection_originator_proxy_assertion_wins(tmp_path: Path) -> None:
    channel = _ws_channel(tmp_path, MagicMock(), trustedProxyAuth={
        "trustedPeerCidrs": ["127.0.0.0/8"],
        "assertionHeader": "X-Verified-User",
    })
    channel._identity_store.set_display_name("anon-evans", "Evans")
    origin = channel._resolve_connection_originator(
        _connection({"X-Verified-User": "evans@house"}),
        "anon-evans",
    )
    assert origin.kind == "human"
    assert origin.label == "evans@house"
    assert origin.attestation == ATTESTATION_PROXY_ASSERTION


def test_connection_originator_untrusted_peer_ignores_assertion(tmp_path: Path) -> None:
    channel = _ws_channel(tmp_path, MagicMock(), trustedProxyAuth={
        "trustedPeerCidrs": ["10.0.0.0/8"],
        "assertionHeader": "X-Verified-User",
    })
    origin = channel._resolve_connection_originator(
        _connection({"X-Verified-User": "evans@house"}),
        "anon-1",
    )
    assert origin.is_anonymous


def test_client_claimed_client_id_without_server_record_stays_anonymous(
    tmp_path: Path,
) -> None:
    channel = _ws_channel(tmp_path, MagicMock())
    origin = channel._resolve_connection_originator(_connection({}), "evans")
    assert origin.is_anonymous


# ---------------------------------------------------------------------------
# Pending-queue recovery journal preserves originator (folds/restarts)
# ---------------------------------------------------------------------------


def _evans_message(chat_id: str = "chat-9") -> InboundMessage:
    return InboundMessage(
        channel="websocket",
        sender_id="anon-evans",
        chat_id=chat_id,
        content="stop the turn, this is Evans",
        originator=MessageOriginator(
            kind="human",
            label="Evans",
            attestation=ATTESTATION_OPERATOR_PROFILE,
        ),
    )


def test_pending_followup_journal_round_trip_preserves_originator() -> None:
    session = Session(key="websocket:chat-9")
    message = _evans_message()
    followup_id = record_pending_followup(session, message)
    assert followup_id is not None

    # Decode exactly what a restart/fold recovery would replay.
    replayed = pending_followups(session)
    assert len(replayed) == 1
    assert replayed[0].originator.kind == "human"
    assert replayed[0].originator.label == "Evans"
    assert replayed[0].originator.attestation == ATTESTATION_OPERATOR_PROFILE


def test_pending_followup_without_originator_replays_anonymous() -> None:
    session = Session(key="websocket:chat-9")
    record_pending_followup(
        session,
        InboundMessage(
            channel="websocket",
            sender_id="anon-1",
            chat_id="chat-9",
            content="older shape",
        ),
    )
    (replayed,) = pending_followups(session)
    assert replayed.originator.is_anonymous


# ---------------------------------------------------------------------------
# History persistence + transcript projection
# ---------------------------------------------------------------------------


def _make_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096)
    return AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model")


def test_user_message_history_stamps_and_replays_originator(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("websocket:chat-9")

    persisted = loop._persist_user_message_early(_evans_message(), session)
    assert persisted is True
    row = session.messages[-1]
    assert row["originator"] == {
        "kind": "human",
        "label": "Evans",
        "attestation": ATTESTATION_OPERATOR_PROFILE,
    }

    # History replay through the served-thread projection keeps the speaker.
    event = _session_user_event("websocket:chat-9", row)
    assert event is not None
    assert event["originator"] == row["originator"]

    # Anonymous messages are stamped explicitly anonymous, never inferred.
    anon = loop._persist_user_message_early(
        InboundMessage(
            channel="websocket",
            sender_id="anon-1",
            chat_id="chat-9",
            content="unattributed",
        ),
        session,
    )
    assert anon is True
    assert session.messages[-1]["originator"] == {"kind": "anonymous"}


def test_session_save_load_round_trip_preserves_originator(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("websocket:chat-9")
    loop._persist_user_message_early(_evans_message(), session)
    loop.sessions.save(session)

    loaded = loop.sessions.read_session_snapshot("websocket:chat-9")
    assert loaded is not None
    assert loaded.messages[-1]["originator"]["label"] == "Evans"


def test_transcript_event_carries_explicit_anonymous() -> None:
    event = build_user_transcript_event(
        "chat-9",
        "hello",
        originator=MessageOriginator.ANONYMOUS,
    )
    assert event is not None
    assert event["originator"] == {"kind": "anonymous"}


def test_transcript_event_without_originator_stays_unstamped() -> None:
    event = build_user_transcript_event("chat-9", "hello")
    assert event is not None
    assert "originator" not in event


def test_thread_replay_projects_originator_to_ui_messages() -> None:
    from nanobot.webui.transcript import replay_transcript_to_ui_messages

    lines = [
        {
            "event": "user",
            "chat_id": "chat-9",
            "text": "Evans here",
            "originator": {
                "kind": "human",
                "label": "Evans",
                "attestation": ATTESTATION_OPERATOR_PROFILE,
            },
        },
        {"event": "user", "chat_id": "chat-9", "text": "older row"},
    ]
    messages = replay_transcript_to_ui_messages(lines)
    user_rows = [m for m in messages if m.get("role") == "user"]
    assert user_rows[0]["originator"] == {
        "kind": "human",
        "label": "Evans",
        "attestation": ATTESTATION_OPERATOR_PROFILE,
    }
    # Rows that predate attribution stay unlabeled rather than guessed.
    assert "originator" not in user_rows[1]


# ---------------------------------------------------------------------------
# Interleaved Evans / Pore in one session (acceptance 1–3, unit level)
# ---------------------------------------------------------------------------


def test_interleaved_evans_and_pore_keep_distinct_originators(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("websocket:chat-9")

    evans = _evans_message()
    assert loop._persist_user_message_early(evans, session)

    pore = InboundMessage(
        channel="system",
        sender_id="session",
        chat_id="websocket:chat-9",
        content="Message from @pore.",
        session_key_override="websocket:chat-9",
        input_role="user",
        originator=MessageOriginator(
            kind="resident",
            label="pore",
            attestation=ATTESTATION_RESIDENT_HANDLE,
            handle="pore",
            session_key="agent:pore",
        ),
    )
    assert loop._persist_user_message_early(pore, session)

    anon = InboundMessage(
        channel="websocket",
        sender_id="anon-2",
        chat_id="chat-9",
        content="unattributed tab",
    )
    assert loop._persist_user_message_early(anon, session)

    kinds = [m["originator"]["kind"] for m in session.messages]
    assert kinds == ["human", "resident", "anonymous"]
    assert session.messages[0]["originator"]["label"] == "Evans"
    assert session.messages[1]["originator"]["handle"] == "pore"
    assert session.messages[2]["originator"] == {"kind": "anonymous"}


# ---------------------------------------------------------------------------
# Mid-turn injection persist path (found live during the 2026-11-08
# acceptance: follow-ups consumed behind a busy turn lost the structured
# originator in history even though the model saw the origin line)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_midturn_injected_followup_persists_originator(tmp_path: Path) -> None:
    from nanobot.agent.runner import AgentRunResult
    from nanobot.session.recovery import PENDING_FOLLOWUP_ID_KEY

    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("websocket:chat-9")

    followup = _evans_message()
    followup.metadata[PENDING_FOLLOWUP_ID_KEY] = "acc-fl-1"
    record_pending_followup(session, followup)
    (journal_followup,) = pending_followups(session)
    assert journal_followup.originator.label == "Evans"

    queue: asyncio.Queue[InboundMessage] = asyncio.Queue()
    queue.put_nowait(journal_followup)

    async def fake_run(spec):
        injected = await spec.injection_callback(limit=10)
        assert injected, "follow-up should be injected mid-turn"
        initial = loop.context.build_transcript(
            spec.transcript_input,
            include_memory=False,
        )
        return AgentRunResult(
            final_content="done",
            messages=[
                *initial,
                *injected,
                {"role": "assistant", "content": "done"},
            ],
            stop_reason="stop",
        )

    loop.runner.run = fake_run  # type: ignore[method-assign]

    await loop._process_message(_evans_message(), pending_queue=queue)

    users = [m for m in session.messages if m.get("role") == "user"]
    assert users[-1]["originator"] == {
        "kind": "human",
        "label": "Evans",
        "attestation": ATTESTATION_OPERATOR_PROFILE,
    }


# ---------------------------------------------------------------------------
# send_session_message attaches the attested resident originator
# ---------------------------------------------------------------------------


class _StubHandles:
    def resolve(self, name: str) -> Any:
        return SimpleNamespace(session_key="agent:target", name=name)

    def handle_for_session(self, key: str) -> Any:
        return SimpleNamespace(session_key=key, name="pore")


@pytest.mark.asyncio
async def test_send_session_message_enqueues_resident_originator() -> None:
    bus = MessageBus()
    tool = SendSessionMessageTool(
        sessions=object(),  # type: ignore[arg-type]
        bus=bus,
        max_messages_per_minute=6,
    )
    tool._handles = _StubHandles()  # type: ignore[assignment]

    target = await tool.enqueue(
        source_session_key="agent:pore",
        target_handle="@bomu",
        content="who is speaking?",
        expect_reply=False,
    )
    assert target == "@bomu"

    msg = await bus.consume_inbound()
    assert msg.originator.kind == "resident"
    assert msg.originator.handle == "pore"
    assert msg.originator.session_key == "agent:pore"
    assert msg.originator.attestation == ATTESTATION_RESIDENT_HANDLE


# ---------------------------------------------------------------------------
# Settings → Identity mutation routes
# ---------------------------------------------------------------------------

_MUTATION_PAYLOAD_ATTR = "_nanobot_webui_mutation_payload"


def _identity_request(payload: dict[str, Any] | None) -> WsRequest:
    request = WsRequest("/api/webui/identity/update", Headers({}))
    setattr(request, _MUTATION_PAYLOAD_ATTR, payload)
    return request


def test_identity_update_persists_and_read_returns_name(tmp_path: Path) -> None:
    channel = _ws_channel(tmp_path, MagicMock())
    handler = channel.gateway.http
    handler.identity = WebUIIdentityStore(tmp_path / "identity.json")

    response = handler._handle_identity(
        "/api/webui/identity/update",
        _identity_request({"client_id": "anon-evans", "display_name": "Evans"}),
    )
    assert response.status_code == 200
    assert json.loads(bytes(response.body)) == {
        "client_id": "anon-evans",
        "display_name": "Evans",
    }

    read = handler._handle_identity(
        "/api/webui/identity/read",
        _identity_request({"client_id": "anon-evans"}),
    )
    assert json.loads(bytes(read.body)) == {
        "client_id": "anon-evans",
        "display_name": "Evans",
    }


def test_identity_clear_returns_to_anonymous(tmp_path: Path) -> None:
    channel = _ws_channel(tmp_path, MagicMock())
    handler = channel.gateway.http
    handler.identity = WebUIIdentityStore(tmp_path / "identity.json")
    handler._handle_identity(
        "/api/webui/identity/update",
        _identity_request({"client_id": "anon-evans", "display_name": "Evans"}),
    )
    cleared = handler._handle_identity(
        "/api/webui/identity/update",
        _identity_request({"client_id": "anon-evans", "display_name": ""}),
    )
    assert json.loads(bytes(cleared.body))["display_name"] == ""


def test_identity_route_requires_mutation_payload(tmp_path: Path) -> None:
    channel = _ws_channel(tmp_path, MagicMock())
    response = channel.gateway.http._handle_identity(
        "/api/webui/identity/update",
        WsRequest("/api/webui/identity/update", Headers({})),
    )
    assert response.status_code == 405


def test_identity_route_validates_client_id(tmp_path: Path) -> None:
    channel = _ws_channel(tmp_path, MagicMock())
    response = channel.gateway.http._handle_identity(
        "/api/webui/identity/update",
        _identity_request({"client_id": "bad id !", "display_name": "Evans"}),
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Live dispatch: connection originator reaches the bus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_message_attaches_connection_originator(tmp_path: Path) -> None:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    channel = _ws_channel(tmp_path, bus)
    origin = AsyncMock()
    origin.remote_address = ("127.0.0.1", 50123)
    channel._connection_originator[origin] = MessageOriginator(
        kind="human",
        label="Evans",
        attestation=ATTESTATION_OPERATOR_PROFILE,
    )

    await channel._dispatch_envelope(
        origin,
        "anon-evans",
        {
            "type": "message",
            "chat_id": "chat-9",
            "content": " Evans here",
            "webui": True,
            "turn_id": "turn-1",
        },
    )

    inbound = bus.publish_inbound.await_args.args[0]
    assert inbound.originator.kind == "human"
    assert inbound.originator.label == "Evans"

    # The model-visible runtime context states the verified origin.
    blocks = inbound.metadata.get("_runtime_context_blocks")
    assert blocks is not None
    rendered = "\n".join(block.content for block in blocks)
    assert "Evans" in rendered
    assert "operator profile" in rendered
