"""Read-only projection of the session material available to the agent."""

from __future__ import annotations

from typing import Any, cast

from nanobot.providers.base import LLMUsage
from nanobot.session.manager import Session
from nanobot.session.summary import summary_continuation_text
from nanobot.utils.helpers import estimate_message_tokens, truncate_text

_SUMMARY_PREVIEW_CHARS = 4_000


def _message_text(content: Any) -> str:
    """Best-effort plain-text view of a replayed message body."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        if parts:
            return "\n".join(parts)
    return str(content)


def session_context_payload(session: Session, *, full: bool = False) -> dict[str, Any]:
    """Return an explainable view of session replay without building a model prompt.

    The final prompt also contains workspace instructions, memory, skills, and a
    model-specific token budget.  This projection deliberately reports only the
    session-owned part: archived summary plus the replayable raw suffix.

    With ``full=True`` the payload carries the complete model-side view: the
    full summary text (not a preview) and every replayed message.  This is the
    operator's window into exactly what accompanies the next prompt.
    """
    replay = session.get_history(max_messages=0, include_runtime_context=False)
    raw_summary = session.metadata.get("_last_summary")
    summary = ""
    summary_preview = ""
    summary_at: str | None = None
    if isinstance(raw_summary, dict):
        summary_data = cast(dict[str, object], raw_summary)
        text = summary_data.get("text")
        last_active = summary_data.get("last_active")
        if isinstance(text, str):
            summary = text.strip()
            summary_preview = truncate_text(summary, _SUMMARY_PREVIEW_CHARS)
        if isinstance(last_active, str):
            summary_at = last_active

    replay_tokens = sum(estimate_message_tokens(message) for message in replay)
    summary_tokens = (
        estimate_message_tokens({"role": "system", "content": summary}) if summary else 0
    )
    stored_usage = LLMUsage.from_dict(session.metadata.get("_last_usage"))
    last_usage = stored_usage.to_turn_dict() if stored_usage is not None else None

    payload: dict[str, Any] = {
        "schema_version": 1,
        "session_key": session.key,
        "total_messages": len(session.messages),
        "archived_messages": min(session.last_archived, len(session.messages)),
        "replay_messages": len(replay),
        "estimated_replay_tokens": replay_tokens,
        "estimated_summary_tokens": summary_tokens,
        "estimated_session_tokens": replay_tokens + summary_tokens,
        "archived_summary": summary_preview or None,
        "archived_summary_at": summary_at,
        "last_usage": last_usage,
    }
    if full:
        payload["archived_summary"] = summary or None
        # ``get_history`` strips private metadata keys, so the checkpoint
        # boundary is identified by its marker content rather than the
        # persisted hidden-history flag.
        payload["replay"] = [
            {
                "role": message.get("role"),
                "content": _message_text(message.get("content")),
                "checkpoint": (
                    message.get("role") == "user"
                    and message.get("content") == summary_continuation_text()
                ),
            }
            for message in replay
        ]
    return payload
