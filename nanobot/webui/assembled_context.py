"""Byte-exact assembled-context view for the operator's window.

The endpoint runs the real assembly (AgentLoop.assembled_context_view) so the
operator sees exactly what the provider call carries — no preview, no
truncation. The loop is registered by the gateway at startup; the handler
stays read-only.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop

_lock = threading.Lock()
_loop_provider: Callable[[], "AgentLoop | None"] | None = None


def set_loop_provider(provider: Callable[[], "AgentLoop | None"] | None) -> None:
    global _loop_provider
    with _lock:
        _loop_provider = provider


def assembled_context_payload(session_key: str) -> dict[str, Any] | None:
    """Assembled context for *session_key*, or None when unavailable."""
    with _lock:
        provider = _loop_provider
    if provider is None:
        return None
    loop = provider()
    if loop is None:
        return None
    return loop.assembled_context_view(session_key)
