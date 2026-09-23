"""Byte-exact assembled-context view: the operator sees the real outbound array."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse


def _make_loop(tmp_path: Path) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.estimate_prompt_tokens.return_value = (10_000, "test")
    provider.chat_stream_with_retry = AsyncMock(return_value=LLMResponse(content="ok", tool_calls=[]))
    provider.generation.max_tokens = 4096
    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        context_window_tokens=128_000,
        session_ttl_minutes=0,
    )
    loop.tools.get_definitions = MagicMock(return_value=[{
        "type": "function",
        "function": {"name": "exec", "description": "Run a command.", "parameters": {}},
    }])
    return loop


def test_assembled_view_returns_real_assembly(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("websocket:chat-1")
    session.add_message("user", "hello from the operator")
    session.add_message("assistant", "hello back")
    loop.sessions.save(session)

    view = loop.assembled_context_view("websocket:chat-1")

    assert view is not None
    assert view["session_key"] == "websocket:chat-1"
    assert view["model"] == "test-model"
    assert view["provider_state_resumable"] is False
    assert view["tools"] == loop.tools.get_definitions()

    messages = view["messages"]
    assert messages[0]["role"] == "system"
    assert "## Runtime" in messages[0]["content"]  # identity template, untruncated
    roles = [m["role"] for m in messages]
    assert roles == ["system", "user", "assistant"]
    assert messages[1]["content"] == "hello from the operator"
    assert messages[2]["content"] == "hello back"


def test_assembled_view_carries_checkpoint_text_in_system_prompt(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("websocket:chat-2")
    session.add_message("user", "archived turn")
    session.commit_summary_checkpoint("working notes from the pass")
    session.add_message("user", "uncovered tail")
    loop.sessions.save(session)

    view = loop.assembled_context_view("websocket:chat-2")

    assert view is not None
    system = view["messages"][0]["content"]
    assert "working notes from the pass" in system
    contents = [m.get("content") for m in view["messages"]]
    assert "archived turn" not in contents  # below the watermark
    assert "uncovered tail" in contents


def test_assembled_view_unknown_session_returns_none(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    assert loop.assembled_context_view("websocket:missing") is None


def test_assembled_view_is_read_only(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("websocket:chat-3")
    session.add_message("user", "before")
    loop.sessions.save(session)

    before = (len(session.messages), session.last_archived, dict(session.metadata))
    loop.assembled_context_view("websocket:chat-3")
    reloaded = loop.sessions.get_or_create("websocket:chat-3")
    after = (len(reloaded.messages), reloaded.last_archived, dict(reloaded.metadata))
    assert before == after
