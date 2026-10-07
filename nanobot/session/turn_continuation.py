"""Turn bookkeeping helpers: the save boundary for inbound user messages.

The sustained-goal system and its internal-continuation machinery were cut
(Evans, 2026-10-07: "Cut the goal system and any reference to it"). What
remains is continuation-agnostic: which inbound messages persist, and where
a turn's history append begins.
"""

from __future__ import annotations

from typing import Any, Mapping

SKIP_USER_PERSIST_META = "_skip_user_persist"


def should_persist_user_message(metadata: Mapping[str, Any] | None) -> bool:
    """Return whether this inbound message should be persisted as user input."""
    return not (metadata and metadata.get(SKIP_USER_PERSIST_META) is True)


def save_skip_for_turn(
    *,
    message_metadata: Mapping[str, Any] | None,
    initial_message_count: int,
    input_persisted_early: bool,
) -> int:
    """Return the persisted-message append boundary for this turn."""
    if message_metadata and message_metadata.get(SKIP_USER_PERSIST_META) is True:
        return initial_message_count
    if not input_persisted_early:
        return initial_message_count - 1
    return initial_message_count
