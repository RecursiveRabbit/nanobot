"""Server-persisted WebUI profile identity (Settings → Identity).

One display name per WebUI profile (client id), stored beside the WebUI
transcripts so a browser refresh, gateway restart, or new tab keeps it. The
name is an operator-controlled identity label — not a permission grant and
not an authentication factor (Evans 2026-11-08). An empty or cleared name
means the profile is explicitly anonymous; messages keep whatever originator
was stamped at ingress, so clearing the name never rewrites history.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, cast

from nanobot.bus.origin import MAX_ORIGIN_LABEL_CHARS, clean_single_line
from nanobot.config.paths import get_webui_dir

_MAX_CLIENT_ID_CHARS = 128
_STORE_FILENAME = "identity.json"


def normalize_display_name(value: Any) -> str:
    """Bound and clean one operator-entered display name; empty clears."""
    return clean_single_line(value, MAX_ORIGIN_LABEL_CHARS)


def normalize_client_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > _MAX_CLIENT_ID_CHARS:
        return None
    if any(ord(char) < 0x21 for char in cleaned):
        return None
    return cleaned


class WebUIIdentityStore:
    """Atomic JSON store mapping WebUI client ids to display names."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (get_webui_dir() / _STORE_FILENAME)
        # RLock: writers hold the lock across the read-modify-write cycle.
        self._lock = threading.RLock()
        # Read-through cache keyed on file identity so independently
        # constructed stores (HTTP route + channel runtime) never go stale.
        self._cache: dict[str, str] | None = None
        self._cache_tag: tuple[int, int] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def display_name_for(self, client_id: str) -> str:
        """Return the persisted display name for one profile, or ""."""
        profiles = self._profiles()
        name = profiles.get(client_id, "")
        return name if isinstance(name, str) else ""

    def set_display_name(self, client_id: str, display_name: str) -> str:
        """Persist (or, when empty, clear) one profile's display name.

        Invalid client ids are refused and never written.
        """
        if normalize_client_id(client_id) is None:
            return ""
        normalized = normalize_display_name(display_name)
        with self._lock:
            profiles = dict(self._profiles())
            if normalized:
                profiles[client_id] = normalized
            else:
                profiles.pop(client_id, None)
            self._write(profiles)
            self._refresh_cache(profiles)
        return normalized

    def _profiles(self) -> dict[str, str]:
        with self._lock:
            try:
                stat = self._path.stat()
                tag = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                stat = None
                tag = None
            if self._cache is not None and tag is not None and tag == self._cache_tag:
                return dict(self._cache)
            if self._cache is not None and tag is None and self._cache_tag is None:
                # File absent and we already know that.
                return dict(self._cache)
            profiles = self._read()
            self._refresh_cache(profiles, tag=tag)
            return dict(profiles)

    def _refresh_cache(
        self,
        profiles: dict[str, str],
        *,
        tag: tuple[int, int] | None = None,
    ) -> None:
        if tag is None:
            try:
                stat = self._path.stat()
                tag = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                tag = None
        self._cache = profiles
        self._cache_tag = tag

    def _read(self) -> dict[str, str]:
        try:
            raw: object = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        data = cast(dict[str, Any], raw)
        profiles = data.get("profiles")
        if not isinstance(profiles, dict):
            return {}
        result: dict[str, str] = {}
        for key, value in cast(dict[str, Any], profiles).items():
            client_id = normalize_client_id(key)
            name = normalize_display_name(value)
            if client_id and name:
                result[client_id] = name
        return result

    def _write(self, profiles: dict[str, str]) -> None:
        payload = {"version": 1, "profiles": profiles}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f"{self._path.name}.",
            suffix=".tmp",
            dir=self._path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self._path)
        finally:
            temporary.unlink(missing_ok=True)
