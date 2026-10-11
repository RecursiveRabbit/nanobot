"""Tests for the context-exceeded strip (Evans 2026-10-10): shears, not a
summary; one strip per session without consent; pause and notify beyond."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.context_governance import (
    CONTEXT_STRIP_METADATA_KEY,
    CONTEXT_STRIP_PLACEHOLDER,
)
from nanobot.agent.loop import AgentLoop
from nanobot.session.manager import Session


def _make_loop(tmp_path):
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    with patch("nanobot.agent.loop.ContextBuilder"), \
         patch("nanobot.agent.loop.SessionManager") as mock_sessions, \
         patch("nanobot.agent.loop.SubagentManager") as mock_sub_mgr:
        mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path)

    loop.sessions = mock_sessions.return_value
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(exist_ok=True)

    def _session_path(key: str):
        return sessions_dir / f"{key.replace(':', '_')}.jsonl"

    loop.sessions._get_session_path = _session_path
    loop.consolidator = MagicMock()
    loop.consolidator.get_lock = lambda key: asyncio.Lock()
    return loop


def _persist(session: Session, loop) -> None:
    """Write the session file the way the turn pipeline would have."""
    import json

    path = loop.sessions._get_session_path(session.key)
    path.write_text(
        "\n".join(json.dumps(message) for message in session.messages) + "\n",
        encoding="utf-8",
    )


def _session_with_tool_results() -> Session:
    return Session(
        key="websocket:test",
        messages=[
            {"role": "user", "content": "read those files"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "name": "read_file",
                "content": "a very large file body",
            },
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call-2",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }],
            },
            {
                "role": "tool",
                "tool_call_id": "call-2",
                "name": "read_file",
                "content": "another large file body",
            },
            {"role": "assistant", "content": "here is what I found"},
        ],
        provider_state=None,
    )


async def test_strip_replaces_every_tool_result_durably(tmp_path):
    loop = _make_loop(tmp_path)
    session = _session_with_tool_results()
    session.provider_state = MagicMock()  # stands in for a ProviderConversationState
    _persist(session, loop)
    session_path = loop.sessions._get_session_path(session.key)
    original_bytes = session_path.read_bytes()

    outcome = await loop._context_strip_for_turn(session, workspace=tmp_path)

    assert outcome.status == "stripped"
    # The desk's boundary: BEFORE any mutation, a verified full-fidelity
    # sibling exists beside the session file, discoverable by ingress.
    siblings = list(session_path.parent.glob(f"{session_path.name}.pre-strip-*.bak"))
    assert len(siblings) == 1
    assert siblings[0].read_bytes() == original_bytes
    meta_preservation = session.metadata[CONTEXT_STRIP_METADATA_KEY][
        "last_preservation"
    ]
    assert meta_preservation == siblings[0].name
    tool_messages = [m for m in session.messages if m.get("role") == "tool"]
    assert [m["content"] for m in tool_messages] == [
        CONTEXT_STRIP_PLACEHOLDER,
        CONTEXT_STRIP_PLACEHOLDER,
    ]
    # Everything else byte-identical.
    assert session.messages[0]["content"] == "read those files"
    assert session.messages[5]["content"] == "here is what I found"
    # Fresh provider request required after rewriting history.
    assert session.provider_state is None
    # The ledger lives in session metadata — one door.
    meta = session.metadata[CONTEXT_STRIP_METADATA_KEY]
    assert meta["count"] == 1
    assert meta["consent_pending"] is False
    assert meta["last_at"]
    loop.sessions.save.assert_called_with(session)
    # The returned history carries the placeholder, not the fat content.
    assert any(
        m.get("content") == CONTEXT_STRIP_PLACEHOLDER for m in outcome.history
    )
    assert all(
        "large file body" not in str(m.get("content")) for m in outcome.history
    )


async def test_strip_is_idempotent_on_placeholders(tmp_path):
    loop = _make_loop(tmp_path)
    session = Session(
        key="websocket:test",
        messages=[{
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "read_file",
            "content": CONTEXT_STRIP_PLACEHOLDER,
        }],
    )

    outcome = await loop._context_strip_for_turn(session, workspace=tmp_path)

    # Nothing unstripped remains: the handler refuses rather than burning
    # the session's one automatic strip on a no-op.
    assert outcome.status == "consent_required"
    assert outcome.reason == "strip-exhausted"
    assert CONTEXT_STRIP_METADATA_KEY not in session.metadata


async def test_strip_single_run_law_requires_consent(tmp_path):
    loop = _make_loop(tmp_path)
    session = _session_with_tool_results()
    session.metadata[CONTEXT_STRIP_METADATA_KEY] = {
        "count": 1,
        "consent_grants": 0,
        "consent_pending": False,
    }

    outcome = await loop._context_strip_for_turn(session, workspace=tmp_path)

    assert outcome.status == "consent_required"
    assert outcome.reason == "strip-consent-required"
    # Nothing was stripped; the pause is flagged; the record exists.
    assert any(
        "large file body" in str(m.get("content")) for m in session.messages
    )
    meta = session.metadata[CONTEXT_STRIP_METADATA_KEY]
    assert meta["consent_pending"] is True
    assert meta["count"] == 1
    assert outcome.error_file is not None
    assert outcome.error_file.startswith("./errors/error_")
    record = tmp_path / "errors" / outcome.error_file.removeprefix("./errors/")
    assert record.exists()
    assert "single-run law" in record.read_text()


async def test_strip_consent_grant_releases_one_more(tmp_path):
    loop = _make_loop(tmp_path)
    session = _session_with_tool_results()
    session.metadata[CONTEXT_STRIP_METADATA_KEY] = {
        "count": 1,
        "consent_grants": 1,
        "consent_pending": True,
    }
    _persist(session, loop)

    outcome = await loop._context_strip_for_turn(session, workspace=tmp_path)

    assert outcome.status == "stripped"
    meta = session.metadata[CONTEXT_STRIP_METADATA_KEY]
    assert meta["count"] == 2
    assert meta["consent_pending"] is False


async def test_strip_exhausted_writes_errors_door_record(tmp_path):
    loop = _make_loop(tmp_path)
    session = Session(
        key="websocket:test",
        messages=[{"role": "user", "content": "a very long prose turn"}],
    )

    outcome = await loop._context_strip_for_turn(session, workspace=tmp_path)

    assert outcome.status == "consent_required"
    assert outcome.reason == "strip-exhausted"
    assert outcome.error_file is not None
    record = tmp_path / "errors" / outcome.error_file.removeprefix("./errors/")
    assert "no unstripped tool results" in record.read_text()


async def test_strip_without_workspace_still_reports(tmp_path):
    loop = _make_loop(tmp_path)
    session = _session_with_tool_results()
    session.metadata[CONTEXT_STRIP_METADATA_KEY] = {"count": 1}

    outcome = await loop._context_strip_for_turn(session, workspace=None)

    assert outcome.status == "consent_required"
    assert outcome.error_file is None


async def test_strip_refused_when_preservation_fails(tmp_path, monkeypatch):
    """No sibling, no strip: the record is never rewritten without its twin."""
    loop = _make_loop(tmp_path)
    session = _session_with_tool_results()
    _persist(session, loop)
    monkeypatch.setattr(
        "nanobot.agent.loop.write_preservation_sibling",
        lambda *args, **kwargs: None,
    )

    outcome = await loop._context_strip_for_turn(session, workspace=tmp_path)

    assert outcome.status == "refused"
    assert outcome.reason == "preservation-unavailable"
    assert any(
        "large file body" in str(m.get("content")) for m in session.messages
    )
    assert CONTEXT_STRIP_METADATA_KEY not in session.metadata
    assert outcome.error_file is not None
    record = tmp_path / "errors" / outcome.error_file.removeprefix("./errors/")
    assert record.exists()


async def test_strip_again_command_grants_consent(tmp_path):
    from nanobot.command.builtin import cmd_strip_again

    loop = _make_loop(tmp_path)
    session = _session_with_tool_results()
    session.metadata[CONTEXT_STRIP_METADATA_KEY] = {
        "count": 1,
        "consent_grants": 0,
        "consent_pending": True,
    }
    ctx = MagicMock()
    ctx.loop = loop
    ctx.session = session
    ctx.msg.channel = "websocket"
    ctx.msg.chat_id = "test"
    ctx.msg.metadata = {}

    reply = await cmd_strip_again(ctx)

    meta = session.metadata[CONTEXT_STRIP_METADATA_KEY]
    assert meta["consent_grants"] == 1
    assert meta["consent_pending"] is False
    assert "grant #1" in reply.content
    loop.sessions.save.assert_called_with(session)
