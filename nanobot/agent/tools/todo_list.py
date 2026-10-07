"""Declarative todo list for the current session's scratchpad."""

# Tool.execute accepts heterogeneous schemas.
# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

from typing import Any, cast

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import RequestContext, ToolContext
from nanobot.agent.tools.runtime_control import JsonValue, RuntimeControl
from nanobot.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema
from nanobot.runtime_context import RuntimeContextBlock

_MAX_ITEMS = 100
_MAX_ITEM_CHARS = 200
_SCRATCHPAD_KEY = "todo_items"


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "Action to perform.",
            enum=("list", "add", "remove", "clear"),
        ),
        item=StringSchema(
            "Text of the item to add. Required when action is 'add'.",
            max_length=_MAX_ITEM_CHARS,
            nullable=True,
        ),
        index=IntegerSchema(
            description="1-based index of the item to remove. Required when action is 'remove'.",
            minimum=1,
            maximum=_MAX_ITEMS,
            nullable=True,
        ),
        required=["action"],
    )
)
class TodoListTool(Tool):
    """Todo list for the current session.

    Here are your current todo items. You can manage your list with the
    following commands: list, add, remove, clear. Items persist across turns
    but not gateway restarts.
    """

    def __init__(self, runtime_control: RuntimeControl) -> None:
        self._runtime_control = runtime_control

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.runtime_control is not None

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.runtime_control is None:
            raise RuntimeError("TodoListTool requires a runtime control capability")
        return cls(runtime_control=ctx.runtime_control)

    @property
    def name(self) -> str:
        return "todo_list"

    @property
    def description(self) -> str:
        return (
            "Todo list for the current session. "
            "Here are your current todo items. You can manage your list with the "
            "following commands: list, add, remove, clear. "
            "Items persist across turns but not gateway restarts."
        )

    def runtime_context_provider(self):
        return self._provide_runtime_context

    async def _provide_runtime_context(
        self,
        request: RequestContext,
    ) -> RuntimeContextBlock | None:
        items = self._current_items()
        if not items:
            return None
        lines = ["Current todo items:"] + [f"{i}. {text}" for i, text in enumerate(items, 1)]
        return RuntimeContextBlock(source="todo_list", content="\n".join(lines))

    async def execute(
        self,
        action: str,
        item: str | None = None,
        index: int | None = None,
        **kwargs: Any,
    ) -> str:
        if action == "list":
            return self._do_list()
        if action == "add":
            return self._do_add(item)
        if action == "remove":
            return self._do_remove(index)
        if action == "clear":
            return self._do_clear()
        return ToolResult.error(f"Error: unknown action '{action}'")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _current_items(self) -> list[str]:
        raw = self._runtime_control.snapshot().scratchpad.get(_SCRATCHPAD_KEY)
        if isinstance(raw, list):
            return [str(x) for x in raw if isinstance(x, str)]
        return []

    def _save_items(self, items: list[str]) -> None:
        self._runtime_control.set_scratchpad(
            _SCRATCHPAD_KEY,
            cast(JsonValue, items),
            max_keys=64,
        )

    def _do_list(self) -> str:
        items = self._current_items()
        if not items:
            return "No todo items."
        lines = [f"{i}. {text}" for i, text in enumerate(items, 1)]
        return "Current todo items:\n" + "\n".join(lines)

    def _do_add(self, item: str | None) -> str:
        if item is None or not item.strip():
            return ToolResult.error("Error: 'item' is required for add and must not be empty.")
        text = item.strip()
        if len(text) > _MAX_ITEM_CHARS:
            return ToolResult.error(
                f"Error: item exceeds {_MAX_ITEM_CHARS} characters."
            )
        items = self._current_items()
        if len(items) >= _MAX_ITEMS:
            return ToolResult.error(f"Error: todo list is full (max {_MAX_ITEMS} items).")
        items.append(text)
        self._save_items(items)
        return f"Added: {text}"

    def _do_remove(self, index: int | None) -> str:
        if index is None:
            return ToolResult.error("Error: 'index' is required for remove.")
        items = self._current_items()
        if not items:
            return ToolResult.error("Error: todo list is empty; nothing to remove.")
        if index < 1 or index > len(items):
            return ToolResult.error(
                f"Error: index {index} is out of range (1-{len(items)})."
            )
        removed = items.pop(index - 1)
        self._save_items(items)
        return f"Removed: {removed}"

    def _do_clear(self) -> str:
        self._save_items([])
        return "Todo list cleared."
