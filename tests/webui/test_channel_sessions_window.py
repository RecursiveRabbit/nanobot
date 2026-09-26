"""The operator's window shows every session, every channel."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from nanobot.session.manager import SessionManager
from nanobot.webui.ws_http import GatewayHTTPHandler


def _handler(workspace: Path) -> GatewayHTTPHandler:
    handler = GatewayHTTPHandler(
        config=MagicMock(),
        session_manager=SessionManager(workspace),
        static_dist_path=None,
        runtime_model_name=None,
        runtime_surface="browser",
        runtime_capabilities_overrides=None,
        bus=MagicMock(),
        tokens=MagicMock(),
        media=MagicMock(),
        ingress=MagicMock(),
        workspaces=MagicMock(),
        settings=MagicMock(),
        skills_workspace_path=workspace,
    )
    handler.workspaces.default_scope.return_value = None
    scope = MagicMock()
    scope.payload.return_value = None
    handler.workspaces.scope_for_indexed_metadata.return_value = scope
    return handler


def _add_session(manager: SessionManager, key: str, text: str) -> None:
    session = manager.get_or_create(key)
    session.add_message("user", text)
    manager.save(session)


def test_sessions_list_includes_channel_sessions(tmp_path: Path) -> None:
    handler = _handler(tmp_path)
    manager = handler.session_manager
    assert manager is not None
    _add_session(manager, "websocket:web-1", "operator chat")
    _add_session(manager, "discord:467239332286300165", "concierge front door")
    _add_session(manager, "cli:direct", "terminal session")

    keys = {row["key"] for row in handler._sessions_list_payload()["sessions"]}

    assert "websocket:web-1" in keys
    assert "discord:467239332286300165" in keys
    assert "cli:direct" in keys


def test_channel_session_thread_synthesizes_transcript(tmp_path: Path) -> None:
    handler = _handler(tmp_path)
    manager = handler.session_manager
    assert manager is not None
    _add_session(manager, "discord:467239332286300165", "visible from the window")
    session = manager.get_or_create("discord:467239332286300165")
    session.add_message("assistant", "concierge reply")
    session.commit_summary_checkpoint("old notes")
    manager.save(session)

    request = MagicMock()
    request.headers = {}
    response = handler._handle_channel_session_thread(
        request, "discord:467239332286300165"
    )

    import json

    body = json.loads(bytes(response.body).decode())
    assert body["channel_session"] is True
    contents = [m["content"] for m in body["messages"]]
    assert "visible from the window" in contents
    assert "concierge reply" in contents
    # The full transcript includes pre-watermark history (it is the log view);
    # hidden markers and commands stay out.
    assert all("Continue the active task" not in c for c in contents)


def test_channel_thread_surfaces_reasoning_and_tool_events(tmp_path: Path) -> None:
    handler = _handler(tmp_path)
    manager = handler.session_manager
    assert manager is not None
    session = manager.get_or_create("discord:main")
    session.add_message("user", "look around")
    session.messages.append({
        "role": "assistant",
        "content": "",
        "reasoning_content": "thinking about the workspace",
        "tool_calls": [{
            "id": "call-1",
            "type": "function",
            "function": {"name": "exec", "arguments": "{\"command\": \"ls\"}"},
        }],
        "timestamp": "2026-09-26T10:00:00",
    })
    session.messages.append({
        "role": "tool",
        "tool_call_id": "call-1",
        "name": "exec",
        "content": "file-a file-b",
        "timestamp": "2026-09-26T10:00:01",
    })
    session.messages.append({
        "role": "assistant",
        "content": "found two files",
        "timestamp": "2026-09-26T10:00:02",
    })
    manager.save(session)

    request = MagicMock()
    request.headers = {}
    response = handler._handle_channel_session_thread(request, "discord:main")

    import json

    body = json.loads(bytes(response.body).decode())
    by_role = [m for m in body["messages"]]
    call_msg = next(m for m in by_role if m.get("toolEvents"))
    assert call_msg["reasoning"] == "thinking about the workspace"
    phases = [e["phase"] for e in call_msg["toolEvents"]]
    assert phases == ["start", "end"]
    assert call_msg["toolEvents"][1]["result"] == "file-a file-b"
    # The folded tool record is not double-rendered as a standalone row.
    assert not any(m["role"] == "tool" for m in by_role)
