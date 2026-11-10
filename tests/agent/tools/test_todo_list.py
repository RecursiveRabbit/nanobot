"""Tests for TodoListTool — declarative session todo list."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.context import (
    RequestContext,
    request_context,
)
from nanobot.agent.tools.runtime_control import AgentRuntimeControl
from nanobot.agent.tools.todo_list import _MAX_ITEM_CHARS, _MAX_ITEMS, TodoListTool
from nanobot.agent.tools.web import WebToolsConfig
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ToolsConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Seat keys chosen to reproduce the 2026-11-10 two-Tuto incident: the pod
# route and the harness route share the handle "tuto" but never the key.
POD_TUTO_KEY = "websocket:tuto"
HARNESS_TUTO_KEY = "websocket:0c417c1f-ba82-4ba2-a969-86fa50ffe6a3"


def _seat(key: str) -> RequestContext:
    return RequestContext(channel="websocket", chat_id="chat-1", session_key=key)


def _bound(key: str):
    """Bind one seat's request context — what the loop does around every turn."""
    return request_context(_seat(key))


@pytest.fixture(autouse=True)
def _bound_seat():
    """Every test runs inside one bound seat, as every production turn does."""
    with request_context(_seat("websocket:test-seat")) as ctx:
        yield ctx

def _make_mock_loop():
    """Build a lightweight mock AgentLoop with the attributes TodoListTool reads."""
    loop = MagicMock()
    loop.model = "test-model"
    loop.max_iterations = 40
    loop.context_window_tokens = 65_536
    loop.workspace = Path("/tmp/workspace")
    loop.provider_retry_mode = "standard"
    loop.max_tool_result_chars = 16_000
    loop.model_preset = None
    loop.model_presets = {}
    loop.tool_names = ["todo_list"]
    loop.web_config = WebToolsConfig()
    loop.exec_config = MagicMock()
    loop.exec_config.enable = True
    loop.exec_config.timeout = 60
    loop.exec_config.path_prepend = ""
    loop.exec_config.path_append = ""
    loop.exec_config.sandbox = "none"
    loop.exec_config.sandbox_ro_binds = []
    loop.exec_config.sandbox_rw_binds = []
    loop.exec_config.allowed_env_keys = []
    loop.exec_config.allow_patterns = []
    loop.exec_config.deny_patterns = []
    loop.subagents = MagicMock()
    loop.subagents.runtime_statuses.return_value = {}
    return loop


def _make_tool(loop=None):
    if loop is None:
        loop = _make_mock_loop()
    return TodoListTool(runtime_control=AgentRuntimeControl(loop))


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

class TestList:

    @pytest.mark.asyncio
    async def test_list_empty(self):
        tool = _make_tool()
        result = await tool.execute(action="list")
        assert result == "No todo items."

    @pytest.mark.asyncio
    async def test_list_numbered_items(self):
        tool = _make_tool()
        await tool.execute(action="add", item="first")
        await tool.execute(action="add", item="second")
        result = await tool.execute(action="list")
        assert "1. first" in result
        assert "2. second" in result


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------

class TestAdd:

    @pytest.mark.asyncio
    async def test_add_item(self):
        tool = _make_tool()
        result = await tool.execute(action="add", item="review the spec")
        assert result == "Added: review the spec"
        assert tool._current_items() == ["review the spec"]

    @pytest.mark.asyncio
    async def test_add_strips_whitespace(self):
        tool = _make_tool()
        result = await tool.execute(action="add", item="  trim me  ")
        assert result == "Added: trim me"
        assert tool._current_items() == ["trim me"]

    @pytest.mark.asyncio
    async def test_add_rejects_empty(self):
        tool = _make_tool()
        result = await tool.execute(action="add", item="   ")
        assert "Error" in result
        assert tool._current_items() == []

    @pytest.mark.asyncio
    async def test_add_rejects_missing_item(self):
        tool = _make_tool()
        result = await tool.execute(action="add")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_add_rejects_oversized_item(self):
        tool = _make_tool()
        big = "x" * (_MAX_ITEM_CHARS + 1)
        result = await tool.execute(action="add", item=big)
        assert "Error" in result
        assert "exceeds" in result

    @pytest.mark.asyncio
    async def test_add_enforces_max_items(self):
        tool = _make_tool()
        for i in range(_MAX_ITEMS):
            await tool.execute(action="add", item=f"item {i}")
        result = await tool.execute(action="add", item="one too many")
        assert "Error" in result
        assert "full" in result


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------

class TestRemove:

    @pytest.mark.asyncio
    async def test_remove_by_index(self):
        tool = _make_tool()
        await tool.execute(action="add", item="first")
        await tool.execute(action="add", item="second")
        result = await tool.execute(action="remove", index=1)
        assert result == "Removed: first"
        assert tool._current_items() == ["second"]

    @pytest.mark.asyncio
    async def test_remove_rejects_missing_index(self):
        tool = _make_tool()
        await tool.execute(action="add", item="only")
        result = await tool.execute(action="remove")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_remove_rejects_zero_index(self):
        tool = _make_tool()
        await tool.execute(action="add", item="only")
        result = await tool.execute(action="remove", index=0)
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_remove_rejects_out_of_range(self):
        tool = _make_tool()
        await tool.execute(action="add", item="only")
        result = await tool.execute(action="remove", index=5)
        assert "Error" in result
        assert "out of range" in result

    @pytest.mark.asyncio
    async def test_remove_from_empty_list(self):
        tool = _make_tool()
        result = await tool.execute(action="remove", index=1)
        assert "Error" in result
        assert "empty" in result


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------

class TestClear:

    @pytest.mark.asyncio
    async def test_clear_empties_list(self):
        tool = _make_tool()
        await tool.execute(action="add", item="task")
        result = await tool.execute(action="clear")
        assert result == "Todo list cleared."
        assert tool._current_items() == []


# ---------------------------------------------------------------------------
# Runtime context provider
# ---------------------------------------------------------------------------

class TestRuntimeContext:

    @pytest.mark.asyncio
    async def test_provider_is_none_when_empty(self):
        tool = _make_tool()
        block = await tool._provide_runtime_context(
            RequestContext(channel="cli", chat_id="direct")
        )
        assert block is None

    @pytest.mark.asyncio
    async def test_provider_lists_items_declaratively(self):
        tool = _make_tool()
        await tool.execute(action="add", item="review the spec")
        await tool.execute(action="add", item="run tests")
        block = await tool._provide_runtime_context(
            RequestContext(channel="cli", chat_id="direct")
        )
        assert block is not None
        assert block.source == "todo_list"
        assert "Current todo items:" in block.content
        assert "1. review the spec" in block.content
        assert "2. run tests" in block.content
        # Declarative: no imperative verbs telling the model what to do.
        lower = block.content.lower()
        assert "you must" not in lower
        assert "do this" not in lower
        assert "complete" not in lower


# ---------------------------------------------------------------------------
# Integration with the tool registry
# ---------------------------------------------------------------------------

def _make_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        tools_config=ToolsConfig(),
    )


class TestRegistry:

    def test_todo_list_tool_is_registered(self, tmp_path: Path):
        loop = _make_loop(tmp_path)
        assert loop.tools.has("todo_list")

    @pytest.mark.asyncio
    async def test_todo_list_via_registry(self, tmp_path: Path):
        loop = _make_loop(tmp_path)
        result = await loop.tools.execute("todo_list", {"action": "add", "item": "registry item"})
        assert "Added" in result
        result = await loop.tools.execute("todo_list", {"action": "list"})
        assert "1. registry item" in result


# ---------------------------------------------------------------------------
# No-cross negative controls — the 2026-11-10 two-Tuto incident must not
# recur: two live seats share the handle "tuto" (pod `websocket:tuto`,
# harness `websocket:0c417c1f-...`), and a guide message intended for the
# pod route arrived at the harness route carrying the other seat's todo
# payload, because the list was one gateway-global scratchpad entry.
# ---------------------------------------------------------------------------

class TestSessionIsolation:

    @pytest.mark.asyncio
    async def test_same_handle_two_seats_do_not_cross(self):
        tool = _make_tool()
        # The pod seat works the Babo lane...
        with _bound(POD_TUTO_KEY):
            await tool.execute(action="add", item="contract agreed (soro)")
        # ...a message landing on the harness route (same handle, different
        # key) must find NONE of it, in either direction.
        with _bound(HARNESS_TUTO_KEY):
            assert await tool.execute(action="list") == "No todo items."
            await tool.execute(action="add", item="lark adoption follow-up")
        with _bound(POD_TUTO_KEY):
            result = await tool.execute(action="list")
        assert "contract agreed (soro)" in result
        assert "lark adoption follow-up" not in result

    @pytest.mark.asyncio
    async def test_provider_block_isolated_per_seat(self):
        # The runtime-context block is how a model SEES the list each turn;
        # the harness route's model must get no block from the pod seat's items.
        tool = _make_tool()
        with _bound(POD_TUTO_KEY):
            await tool.execute(action="add", item="babo office CLI")
        block = await tool._provide_runtime_context(_seat(HARNESS_TUTO_KEY))
        assert block is None

    @pytest.mark.asyncio
    async def test_no_session_key_falls_back_without_touching_seats(self):
        # A caller with no session key (chat_id fallback) gets its own bucket
        # and cannot see — or wipe — a named seat's list.
        tool = _make_tool()
        with _bound(POD_TUTO_KEY):
            await tool.execute(action="add", item="pod seat item")
        with request_context(RequestContext(channel="cli", chat_id="elsewhere")):
            result = await tool.execute(action="list")
        assert result == "No todo items."
        with _bound(POD_TUTO_KEY):
            assert "pod seat item" in await tool.execute(action="list")
