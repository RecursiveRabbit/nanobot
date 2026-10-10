"""Helpers for validated session-summary metadata."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypedDict, cast

from nanobot.session.history_visibility import is_hidden_history_message

# Marker lines that compaction once inserted as hidden user-turn messages at
# the archive boundary. Pulled by the root on Evans' 2026-10-10 ruling: the
# harness never places synthetic text into user turns (puppeting is a
# cardinal sin), and the checkpoint content already rides the system
# prompt's archived-summary block. Nothing creates these lines anymore; the
# set exists ONLY as a suture so checkpoints persisted before the excision
# keep their marker out of replay and summarization.
_RETIRED_CHECKPOINT_MARKER_TEXTS = frozenset({
    "Continue the active task from the working-memory checkpoint above.",
    "Working-memory checkpoint above.",
})


def is_retired_summary_checkpoint(message: Mapping[str, Any] | None) -> bool:
    """Identify a retired checkpoint marker in persisted history.

    Suture only: new checkpoints write no marker message at all. Old sessions
    still carry one of these hidden lines; matching them here keeps the
    retired text out of model replay (it would otherwise surface as a user
    turn) and out of what the consolidator feeds the summarizer.
    """
    if not is_hidden_history_message(message):
        return False
    return message.get("content") in _RETIRED_CHECKPOINT_MARKER_TEXTS


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
