"""Helpers for validated session-summary metadata."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypedDict, cast

from nanobot.session.history_visibility import is_hidden_history_message
from nanobot.utils.strings import register_literal, text as string_text

SUMMARY_CONTINUATION_TEXT = register_literal(
    "literal:summary_continuation",
    "Continue the active task from the working-memory checkpoint above.",
    group="Compaction",
)

def summary_continuation_text() -> str:
    """The current continuation marker (operator-configurable)."""
    return string_text("literal:summary_continuation", SUMMARY_CONTINUATION_TEXT)

def is_summary_checkpoint(message: Mapping[str, Any]) -> bool:
    """Identify the durable boundary of a replacement summary.

    Matches both the bundled marker and any operator override, so overriding
    the string never orphans checkpoints written under the previous text.
    """
    if not is_hidden_history_message(message):
        return False
    content = message.get("content")
    return content == SUMMARY_CONTINUATION_TEXT or content == summary_continuation_text()


class SessionSummary(TypedDict):
    text: str
    last_active: str


@dataclass(frozen=True, slots=True)
class SessionSummaryCheckpoint:
    """A replacement summary and the raw transcript boundary it covers."""

    summary: str
    transcript_boundary: int


def session_summary_from_metadata(
    metadata: Mapping[str, object] | None,
    *,
    fallback_last_active: datetime,
) -> SessionSummary | None:
    raw: object = metadata.get("_last_summary") if metadata is not None else None
    if not isinstance(raw, Mapping):
        return None
    summary_data = cast(Mapping[str, object], raw)
    text = summary_data.get("text")
    if not isinstance(text, str) or not text:
        return None
    raw_last_active = summary_data.get("last_active")
    if isinstance(raw_last_active, str):
        try:
            datetime.fromisoformat(raw_last_active)
            last_active = raw_last_active
        except ValueError:
            last_active = fallback_last_active.isoformat()
    else:
        last_active = fallback_last_active.isoformat()
    return {"text": text, "last_active": last_active}
