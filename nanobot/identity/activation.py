"""Agent activation gate: inert sessions, deliberate activation.

Evans 2026-11-08 (the Munu incident): a chat session is not an agent, and
agent creation must never happen incidentally as a side effect of reading
a session, resolving a raw key, sending a message, or running a test.

The harness-side contract:

- An activation registry (JSON, written ONLY by the Manager onboarding
  workflow — dova/soro's lane) records each agent's state and approved
  surfaces: ``resident_messaging``, ``fold``, ``baton``, ``valley``,
  ``autonomous_work``.
- The harness READS the registry (mtime-cached, zero write paths). When
  no registry is configured the gate is disabled and behavior is
  unchanged — the desk flips the gate by placing the v1 image.
- Once a registry exists, sessions with no entry are INERT: they may
  exist as transport addressing, but resident messaging refuses them,
  folds never arm, baton ceremonies do not run, Deal days do not open
  (no Valley presence), and cron/automation turns are dropped.

Activation is a recorded Manager decision. Nothing in the harness —
read, resolve, send, fold — can create or modify an activation entry.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Mapping, cast

SURFACES: tuple[str, ...] = (
    "resident_messaging",
    "fold",
    "baton",
    "valley",
    "autonomous_work",
)

ENV_REGISTRY_PATH = "NANOBOT_ACTIVATION_REGISTRY"
_DEFAULT_FILENAME = "activation.json"


def _clean(value: Any, max_chars: int = 128) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:max_chars]


@dataclass(frozen=True)
class AgentActivation:
    """One agent's activation record, exactly as the Manager wrote it."""

    handle: str
    session_key: str = ""
    state: str = "inert"  # "inert" | "active"
    approved_by: str = ""
    purpose: str = ""
    activated_at: str = ""
    surfaces: frozenset[str] = frozenset()

    def allows(self, surface: str) -> bool:
        return self.state == "active" and surface in self.surfaces


@dataclass(frozen=True)
class ActivationDecision:
    """What the harness may conclude about one session."""

    active: bool = False
    activation: AgentActivation | None = None

    def allows(self, surface: str) -> bool:
        return self.activation is not None and self.activation.allows(surface)


class ActivationStore:
    """Read-through, mtime-cached view of the activation registry.

    The harness never writes this file. The Manager onboarding workflow
    owns the path (``NANOBOT_ACTIVATION_REGISTRY`` or the data-dir
    default); a missing file means the gate is disabled.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._cache: dict[str, Any] | None = None
        self._cache_tag: tuple[int, int] | None = None

    @classmethod
    def default_path(cls) -> Path | None:
        raw = os.environ.get(ENV_REGISTRY_PATH, "").strip()
        if raw:
            return Path(raw).expanduser()
        try:
            from nanobot.config.paths import get_data_dir

            candidate = get_data_dir() / _DEFAULT_FILENAME
            return candidate if candidate.is_file() else None
        except Exception:
            return None

    @classmethod
    def live(cls) -> "ActivationStore":
        """Shared instance for enforcement seams (tests may replace it)."""
        store = cls._live
        if store is None:
            store = cls(cls.default_path())
            cls._live = store
        return store

    _live: ClassVar["ActivationStore | None"] = None

    @classmethod
    def reset_live(cls, store: "ActivationStore | None" = None) -> None:
        cls._live = store

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def gate_enabled(self) -> bool:
        """No registry configured → gate disabled, behavior unchanged."""
        return self._path is not None

    def decide(self, *, handle: str | None = None, session_key: str | None = None) -> ActivationDecision:
        """Resolve one session. Unknown sessions are inert once the gate
        exists; when the gate is disabled everything is allowed (current
        behavior)."""
        if not self.gate_enabled:
            return ActivationDecision(active=True)
        agents = self._agents()
        record = None
        if handle:
            record = agents.get(f"handle:{_clean(handle)}")
        if record is None and session_key:
            record = agents.get(f"key:{_clean(session_key, 512)}")
        if record is None:
            return ActivationDecision(active=False)
        return ActivationDecision(active=record.state == "active", activation=record)

    def is_active_for(self, session_key: str, surface: str, handle: str | None = None) -> bool:
        if not self.gate_enabled:
            return True  # gate disabled: behavior unchanged
        return self.decide(handle=handle, session_key=session_key).allows(surface)

    # -- registry reading -------------------------------------------------

    def _agents(self) -> dict[str, AgentActivation]:
        with self._lock:
            raw = self._load()
            return self._parse(raw)

    def _load(self) -> object:
        path = self._path
        if path is None:
            return None
        try:
            stat = path.stat()
            tag = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            tag = None
        if self._cache is not None and tag is not None and tag == self._cache_tag:
            return self._cache
        if self._cache is not None and tag is None and self._cache_tag is None:
            return self._cache
        try:
            payload: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            payload = None
        self._cache = payload
        self._cache_tag = tag
        return payload

    @staticmethod
    def _parse(payload: object) -> dict[str, AgentActivation]:
        if not isinstance(payload, Mapping):
            return {}
        agents = payload.get("agents")
        if not isinstance(agents, list):
            return {}
        result: dict[str, AgentActivation] = {}
        for item in cast(list[Any], agents):
            if not isinstance(item, Mapping):
                continue
            data = cast(Mapping[str, Any], item)
            handle = _clean(data.get("handle"))
            session_key = _clean(data.get("session_key"), 512)
            if not handle and not session_key:
                continue
            surfaces_raw = data.get("surfaces")
            surfaces = frozenset(
                surface for surface in cast(list[Any], surfaces_raw)
                if isinstance(surface, str) and surface in SURFACES
            ) if isinstance(surfaces_raw, list) else frozenset()
            record = AgentActivation(
                handle=handle,
                session_key=session_key,
                state="active" if data.get("state") == "active" else "inert",
                approved_by=_clean(data.get("approved_by")),
                purpose=_clean(data.get("purpose"), 512),
                activated_at=_clean(data.get("activated_at")),
                surfaces=surfaces,
            )
            if handle:
                result[f"handle:{handle}"] = record
            if session_key:
                result.setdefault(f"key:{session_key}", record)
        return result

    # -- image construction (Manager workflow / snapshot tooling) ----------

    @staticmethod
    def build_image(entries: list[Mapping[str, Any]]) -> dict[str, Any]:
        """Assemble a registry payload for the Manager workflow to write.

        Harness code never calls this in production; it exists so the
        onboarding workflow and snapshot tooling share one schema.
        """
        agents: list[dict[str, Any]] = []
        for entry in entries:
            handle = _clean(entry.get("handle"))
            if not handle:
                raise ValueError("activation entries require a handle")
            surfaces_raw = entry.get("surfaces")
            surfaces = (
                [s for s in cast(list[Any], surfaces_raw) if isinstance(s, str) and s in SURFACES]
                if isinstance(surfaces_raw, list)
                else []
            )
            agents.append({
                "handle": handle,
                "session_key": _clean(entry.get("session_key"), 512),
                "state": "active" if entry.get("state") == "active" else "inert",
                "approved_by": _clean(entry.get("approved_by")),
                "purpose": _clean(entry.get("purpose"), 512),
                "activated_at": _clean(entry.get("activated_at")),
                "surfaces": surfaces,
            })
        return {"version": 1, "agents": agents}


def activation_store() -> ActivationStore:
    """The live store enforcement seams consult."""
    return ActivationStore.live()
