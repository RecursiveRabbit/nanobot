"""Memory storage, transcript archiving, and session checkpoint consolidation."""

# Tool schemas are installed by the ``@tool_parameters`` class decorator at
# runtime; static analyzers cannot observe that it clears ``parameters`` from
# ``__abstractmethods__`` before these classes are instantiated.
# pyright: reportAbstractUsage=false, reportPrivateUsage=false

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import weakref
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator, cast
from uuid import uuid4

from loguru import logger

from nanobot.events import NO_EVENTS, ContextCompactionEvent, EventSink
from nanobot.llm_usage.context import llm_usage_source
from nanobot.providers.base import ProviderCallContext, ProviderConversationState
from nanobot.runtime_context import public_history_messages
from nanobot.session.manager import Session, SessionManager
from nanobot.session.summary import is_summary_checkpoint, session_summary_from_metadata
from nanobot.utils.gitstore import GitStore
from nanobot.utils.helpers import (
    content_with_media_breadcrumbs,
    ensure_dir,
    estimate_prompt_tokens_chain,
    strip_think,
)
from nanobot.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from nanobot.agent.tools.registry import ToolRegistry
    from nanobot.utils.llm_runtime import LLMRuntime

# ---------------------------------------------------------------------------
# MemoryStore — pure file I/O layer
# ---------------------------------------------------------------------------


class MemoryStore:
    """Pure file I/O for memory files: MEMORY.md, history.jsonl, SOUL.md, USER.md."""

    _DEFAULT_MAX_HISTORY = 1000
    _LEGACY_ENTRY_START_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}[^\]]*)\]\s*")
    _LEGACY_TIMESTAMP_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]\s*")
    _LEGACY_RAW_MESSAGE_RE = re.compile(
        r"^\[\d{4}-\d{2}-\d{2}[^\]]*\]\s+[A-Z][A-Z0-9_]*(?:\s+\[tools:\s*[^\]]+\])?:"
    )

    def __init__(self, workspace: Path, max_history_entries: int = _DEFAULT_MAX_HISTORY):
        self.workspace = workspace
        self.max_history_entries = max_history_entries
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "history.jsonl"
        self.legacy_history_file = self.memory_dir / "HISTORY.md"
        self.soul_file = workspace / "SOUL.md"
        self.user_file = workspace / "USER.md"
        self._cursor_file = self.memory_dir / ".cursor"
        self._corruption_logged = False  # rate-limit invalid cursor warning
        self._malformed_entry_logged = False  # rate-limit bad history shape warning
        self._append_lock = threading.Lock()  # serialize cursor allocation + append
        self._git = GitStore(workspace, tracked_files=[
            "SOUL.md", "USER.md", "memory/MEMORY.md",
        ])
        self._maybe_migrate_legacy_history()

    @property
    def git(self) -> GitStore:
        return self._git

    # -- generic helpers -----------------------------------------------------

    @staticmethod
    def read_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def _maybe_migrate_legacy_history(self) -> None:
        """One-time upgrade from legacy HISTORY.md to history.jsonl.

        The migration is best-effort and prioritizes preserving as much content
        as possible over perfect parsing.
        """
        if not self.legacy_history_file.exists():
            return
        if self.history_file.exists() and self.history_file.stat().st_size > 0:
            return

        try:
            legacy_text = self.legacy_history_file.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            logger.exception("Failed to read legacy HISTORY.md for migration")
            return

        entries = self._parse_legacy_history(legacy_text)
        try:
            if entries:
                self._write_entries(entries)
                last_cursor = entries[-1]["cursor"]
                self._cursor_file.write_text(str(last_cursor), encoding="utf-8")

            backup_path = self._next_legacy_backup_path()
            self.legacy_history_file.replace(backup_path)
            logger.info(
                "Migrated legacy HISTORY.md to history.jsonl ({} entries)",
                len(entries),
            )
        except Exception:
            logger.exception("Failed to migrate legacy HISTORY.md")

    def _parse_legacy_history(self, text: str) -> list[dict[str, Any]]:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return []

        fallback_timestamp = self._legacy_fallback_timestamp()
        entries: list[dict[str, Any]] = []
        chunks = self._split_legacy_history_chunks(normalized)

        for cursor, chunk in enumerate(chunks, start=1):
            timestamp = fallback_timestamp
            content = chunk
            match = self._LEGACY_TIMESTAMP_RE.match(chunk)
            if match:
                timestamp = match.group(1)
                remainder = chunk[match.end():].lstrip()
                if remainder:
                    content = remainder

            entries.append({
                "cursor": cursor,
                "timestamp": timestamp,
                "content": content,
            })
        return entries

    def _split_legacy_history_chunks(self, text: str) -> list[str]:
        lines = text.split("\n")
        chunks: list[str] = []
        current: list[str] = []
        saw_blank_separator = False

        for line in lines:
            if saw_blank_separator and line.strip() and current:
                chunks.append("\n".join(current).strip())
                current = [line]
                saw_blank_separator = False
                continue
            if self._should_start_new_legacy_chunk(line, current):
                chunks.append("\n".join(current).strip())
                current = [line]
                saw_blank_separator = False
                continue
            current.append(line)
            saw_blank_separator = not line.strip()

        if current:
            chunks.append("\n".join(current).strip())
        return [chunk for chunk in chunks if chunk]

    def _should_start_new_legacy_chunk(self, line: str, current: list[str]) -> bool:
        if not current:
            return False
        if not self._LEGACY_ENTRY_START_RE.match(line):
            return False
        if self._is_raw_legacy_chunk(current) and self._LEGACY_RAW_MESSAGE_RE.match(line):
            return False
        return True

    def _is_raw_legacy_chunk(self, lines: list[str]) -> bool:
        first_nonempty = next((line for line in lines if line.strip()), "")
        match = self._LEGACY_TIMESTAMP_RE.match(first_nonempty)
        if not match:
            return False
        return first_nonempty[match.end():].lstrip().startswith("[RAW]")

    def _legacy_fallback_timestamp(self) -> str:
        try:
            return datetime.fromtimestamp(
                self.legacy_history_file.stat().st_mtime,
            ).strftime("%Y-%m-%d %H:%M")
        except OSError:
            return datetime.now().strftime("%Y-%m-%d %H:%M")

    def _next_legacy_backup_path(self) -> Path:
        candidate = self.memory_dir / "HISTORY.md.bak"
        suffix = 2
        while candidate.exists():
            candidate = self.memory_dir / f"HISTORY.md.bak.{suffix}"
            suffix += 1
        return candidate

    # -- MEMORY.md (long-term facts) -----------------------------------------

    def read_memory(self) -> str:
        return self.read_file(self.memory_file)

    def write_memory(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    # -- SOUL.md -------------------------------------------------------------

    def read_soul(self) -> str:
        return self.read_file(self.soul_file)

    def write_soul(self, content: str) -> None:
        self.soul_file.write_text(content, encoding="utf-8")

    # -- USER.md -------------------------------------------------------------

    def read_user(self) -> str:
        return self.read_file(self.user_file)

    def write_user(self, content: str) -> None:
        self.user_file.write_text(content, encoding="utf-8")

    # -- context injection (used by context.py) ------------------------------

    def get_memory_context(self) -> str:
        long_term = self.read_memory()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    # -- history.jsonl — append-only, JSONL format ---------------------------

    def _normalize_history_entry(
        self,
        entry: str,
        *,
        max_chars: int | None = None,
    ) -> str:
        """Return the model-safe text accepted by the journal.

        Evans, 2026-09-24: never truncate. There is no size cap — length is
        not a validity signal ("if you're using response size as a metric to
        try and detect failure, you're not tracking what you say you're
        tracking"). The max_chars parameter is retired and ignored.
        """
        return strip_think(entry.rstrip())

    def append_history(
        self,
        entry: str,
        *,
        max_chars: int | None = None,
        session_key: str | None = None,
    ) -> int:
        """Append *entry* to history.jsonl and return its auto-incrementing cursor.

        Entries are passed through `strip_think` to drop template-level leaks
        (e.g. unclosed `<think` prefixes, `<channel|>` markers) before being
        persisted. If the cleaned content is empty but the raw entry wasn't,
        the record is persisted with an empty string rather than falling back
        to the raw leak — otherwise `strip_think`'s guarantees would be
        undone when the journal entry is consumed downstream.

        No length cap. Evans, 2026-09-24: an agent may return its whole
        context as a summary if it chooses; nothing here measures size.
        """
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        raw = entry.rstrip()
        content = self._normalize_history_entry(entry, max_chars=max_chars)
        # Cursor allocation and the append must be atomic: concurrent writers
        # could otherwise read the same current cursor and emit duplicates.
        with self._append_lock:
            cursor = self._next_cursor()
            if raw and not content:
                logger.debug(
                    "history entry {} stripped to empty (likely template leak); "
                    "persisting empty content to avoid re-polluting journal consumers",
                    cursor,
                )
            record = {"cursor": cursor, "timestamp": ts, "content": content}
            if session_key:
                record["session_key"] = session_key
            with open(self.history_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._cursor_file.write_text(str(cursor), encoding="utf-8")
        return cursor

    @staticmethod
    def _valid_cursor(value: Any) -> int | None:
        """Non-negative int cursors only; reject bool (``isinstance(True, int)`` is True)."""
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    def _iter_valid_entries(self) -> Iterator[tuple[dict[str, Any], int]]:
        """Yield ``(entry, cursor)`` for well-formed entries; warn once on corruption."""
        poisoned: Any = None
        malformed_cursor: int | None = None
        for entry in self._read_entries():
            raw = entry.get("cursor")
            if raw is None:
                continue
            cursor = self._valid_cursor(raw)
            if cursor is None:
                poisoned = raw
                continue
            if not self._valid_history_payload(entry):
                malformed_cursor = cursor
                continue
            yield entry, cursor
        if poisoned is not None and not self._corruption_logged:
            self._corruption_logged = True
            logger.warning(
                "history.jsonl contains an invalid cursor ({!r}); dropping it. "
                "Usually caused by an external writer; further occurrences suppressed.",
                poisoned,
            )
        if malformed_cursor is not None and not self._malformed_entry_logged:
            self._malformed_entry_logged = True
            logger.warning(
                "history.jsonl contains a malformed entry at cursor {}; dropping it. "
                "Usually caused by an external writer; further occurrences suppressed.",
                malformed_cursor,
            )

    @staticmethod
    def _valid_history_payload(entry: dict[str, Any]) -> bool:
        if not isinstance(entry.get("timestamp"), str):
            return False
        if not isinstance(entry.get("content"), str):
            return False
        session_key = entry.get("session_key")
        return session_key is None or isinstance(session_key, str)

    def _read_cursor_counter(self) -> int | None:
        """Return the persisted cursor counter when it is usable."""
        if not self._cursor_file.exists():
            return None
        with suppress(ValueError, OSError):
            cursor = int(self._cursor_file.read_text(encoding="utf-8").strip())
            if cursor >= 0:
                return cursor
        return None

    def _next_cursor(self) -> int:
        """Read the current cursor counter and return the next value."""
        cursor_counter = self._read_cursor_counter()
        last = self._read_last_entry() or {}
        last_cursor = self._valid_cursor(last.get("cursor"))
        if cursor_counter is not None:
            if last_cursor is not None:
                return max(cursor_counter, last_cursor) + 1
            max_history_cursor = max((c for _, c in self._iter_valid_entries()), default=0)
            return max(cursor_counter, max_history_cursor) + 1

        # Fast path: trust the tail when intact.  Otherwise scan the whole
        # file and take ``max`` — that stays correct even if the monotonic
        # invariant was broken by external writes.
        if last_cursor is not None:
            return last_cursor + 1
        return max((c for _, c in self._iter_valid_entries()), default=0) + 1

    def read_unprocessed_history(self, since_cursor: int) -> list[dict[str, Any]]:
        """Return history entries with a valid cursor > *since_cursor*."""
        return [e for e, c in self._iter_valid_entries() if c > since_cursor]

    # -- JSONL helpers -------------------------------------------------------

    def _read_entries(self) -> list[dict[str, Any]]:
        """Read all entries from history.jsonl."""
        entries: list[dict[str, Any]] = []
        with suppress(FileNotFoundError):
            with open(self.history_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            parsed: object = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(parsed, dict):
                            entries.append(cast(dict[str, Any], parsed))

        return entries

    def _read_last_entry(self) -> dict[str, Any] | None:
        """Read the last entry from the JSONL file efficiently."""
        try:
            with open(self.history_file, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size == 0:
                    return None
                read_size = min(size, 4096)
                f.seek(size - read_size)
                data = f.read().decode("utf-8")
                lines = [line for line in data.split("\n") if line.strip()]
                if not lines:
                    return None
                parsed: object = json.loads(lines[-1])
                return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else None
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _write_entries(self, entries: list[dict[str, Any]]) -> None:
        """Overwrite history.jsonl with the given entries (atomic write)."""
        tmp_path = self.history_file.with_suffix(self.history_file.suffix + ".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                for entry in entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.history_file)

            # fsync the directory so the rename is durable.
            # On Windows, opening a directory with O_RDONLY raises
            # PermissionError — skip the dir sync there (NTFS
            # journals metadata synchronously).
            with suppress(PermissionError):
                fd = os.open(str(self.history_file.parent), os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    def get_latest_cursor(self) -> int:
        return max(self._next_cursor() - 1, 0)

# ---------------------------------------------------------------------------
# Memory ingestion and context-pressure coordination
# ---------------------------------------------------------------------------




def _narrative_view(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop tool traffic from a summary-input assembly (Evans 2026-09-29).

    Tool results are the bulk and the least of the narrative; assistant
    tool_calls go with them so no provider sees an unpaired call. The
    stored session record is untouched — this view exists only as
    summarizer input.
    """
    narrative: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "tool":
            continue
        if "tool_calls" in message:
            message = {key: value for key, value in message.items() if key != "tool_calls"}
        narrative.append(message)
    return narrative


class MemoryArchiver:
    """Write durable transcript batches to the Memory ingestion journal.

    The archiver deliberately has no SessionManager dependency: it may read a
    captured transcript batch and append to history.jsonl, but it cannot mutate
    provider continuation state or advance a session watermark.
    """

    def __init__(
        self,
        store: MemoryStore,
        build_messages: Callable[..., list[dict[str, Any]]],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        resolve_prompt_context: Callable[[Session], tuple[str | None, Path | None]] | None = None,
        escalate_runtime: Callable[[int, LLMRuntime], LLMRuntime | None] | None = None,
    ) -> None:
        self.store = store
        self._build_messages = build_messages
        self._get_tool_definitions = get_tool_definitions
        self._resolve_prompt_context = resolve_prompt_context
        self._escalate_runtime = escalate_runtime


    async def archive(
        self,
        source_messages: list[dict[str, Any]],
        *,
        runtime: LLMRuntime,
        session_key: str,
        history: list[dict[str, Any]],
        request_tools: list[dict[str, Any]],
        previous_summary: str | None = None,
        input_token_budget: int | None = None,
        fallback_max_tokens: int | None = None,
        provider_state: ProviderConversationState | None = None,
    ) -> str | None:
        """Append the archive prompt to H and persist its summary."""
        if not source_messages:
            return None

        prompt = render_template(
            "agent/consolidator_archive.md",
            strip=True,
            archive_count=len(source_messages),
        )
        prompt_message = {"role": "user", "content": prompt}
        provider_context = None
        call_tools = request_tools
        if provider_state is not None:
            if not runtime.provider.can_resume_conversation_state(
                provider_state,
                runtime.model,
            ):
                logger.warning(
                    "Memory archive cannot resume provider state for {}; failing the pass",
                    session_key,
                )
                return None
            instruction_messages: list[dict[str, Any]] = []
            for message in history:
                if message.get("role") not in {"system", "developer"}:
                    break
                instruction_messages.append(dict(message))
            request_messages = [*instruction_messages, prompt_message]
            provider_context = ProviderCallContext(
                conversation_state=provider_state.with_pending_messages([
                    *provider_state.pending_messages,
                    prompt_message,
                ]),
                context_window_tokens=runtime.context_window_tokens,
                session_id=session_key,
            )
            call_tools = []
        else:
            request_messages = [
                *[dict(message) for message in history],
                prompt_message,
            ]
        if input_token_budget is not None and provider_context is None:
            estimated, source = estimate_prompt_tokens_chain(
                runtime.provider,
                runtime.model,
                request_messages,
                call_tools,
            )
            if input_token_budget <= 0 or estimated > input_token_budget:
                logger.warning(
                    "Memory archive input does not fit for {}: {}/{} via {}; failing the pass",
                    session_key,
                    estimated,
                    input_token_budget,
                    source,
                )
                return None

        try:
            with llm_usage_source("system"):
                response = await runtime.provider.chat_stream_with_retry(
                    model=runtime.model,
                    messages=request_messages,
                    tools=call_tools,
                    temperature=runtime.generation.temperature,
                    max_tokens=runtime.generation.max_tokens,
                    reasoning_effort=runtime.generation.reasoning_effort,
                    provider_context=provider_context,
                )
        except Exception:
            logger.warning("Memory archive provider call failed; failing the pass")
            return None
        if response.finish_reason in {"error", "length"}:
            logger.warning(
                "Memory archive provider did not complete ({}); failing the pass",
                response.finish_reason,
            )
            return None
        if response.has_tool_calls is True:
            logger.warning("Memory archive provider returned tool calls; failing the pass")
            return None
        summary = response.content
        if not summary or not summary.strip():
            logger.warning("Memory archive provider returned no summary; failing the pass")
            return None
        if summary.strip() == "(nothing)":
            # The retired archive contract offered "(nothing)" as a sanctioned
            # response — total amnesia on demand. Treat it as a failed pass.
            logger.warning("Memory archive provider returned retired (nothing) sentinel; failing the pass")
            return None
        summary = strip_think(summary).strip()
        if not summary:
            logger.warning("Memory archive summary empty after normalization; failing the pass")
            return None
        self.store.append_history(summary, session_key=session_key)
        return summary

    async def archive_session(
        self,
        session: Session,
        *,
        archive_end: int,
        runtime: LLMRuntime,
        input_token_budget: int,
    ) -> str | None:
        """Archive a captured session prefix without mutating the session."""
        messages = [
            message for message in session.messages[session.last_archived:archive_end]
            if not message.get("_command") and not is_summary_checkpoint(message)
        ]
        if not messages:
            return None
        session_summary = session_summary_from_metadata(
            session.metadata,
            fallback_last_active=session.updated_at,
        )
        previous_summary = session_summary["text"] if session_summary else None

        # ONE READER (Evans, 2026-09-29): the summarizer consumes the
        # session's OWN assembly — the same get_history the turn pipeline
        # uses. A session that can process on its history is valid by
        # definition; there is no second, stricter view to veto it. The old
        # seam check tested the wrong truth and bricked discord:main.
        prefix = Session(
            key=session.key,
            messages=list(session.messages[:archive_end]),
            last_consolidated=session.last_archived,
        )
        history = _narrative_view(prefix.get_history())

        channel = session.key.split(":", 1)[0] if ":" in session.key else None
        workspace: Path | None = None
        if self._resolve_prompt_context is not None:
            channel, workspace = self._resolve_prompt_context(session)
        history_messages = self._build_messages(
            history=history,
            current_message=None,
            channel=channel,
            session_summary=session_summary,
            workspace=workspace,
        )
        tools = self._get_tool_definitions()

        # AUTO-ESCALATE (Evans, same ruling): if the assembled input
        # exceeds the session's own budget, the pass borrows the smallest
        # house tier that fits. The session's pin is untouched.
        estimated, _estimate_source = estimate_prompt_tokens_chain(
            runtime.provider,
            runtime.model,
            history_messages,
            tools,
        )
        if estimated > input_token_budget and self._escalate_runtime is not None:
            escalated = self._escalate_runtime(estimated, runtime)
            if escalated is not None:
                escalated_budget = (
                    escalated.context_window_tokens
                    - max(0, escalated.generation.max_tokens)
                    - 1024
                )
                if estimated <= escalated_budget:
                    logger.info(
                        "Memory archive escalating {} -> {} for {} (~{} tokens)",
                        runtime.model,
                        escalated.model,
                        session.key,
                        estimated,
                    )
                    runtime = escalated
                    input_token_budget = escalated_budget
        if input_token_budget <= 0 or estimated > input_token_budget:
            logger.warning(
                "Memory archive input does not fit any available tier for {}: ~{}/{}; failing the pass",
                session.key,
                estimated,
                input_token_budget,
            )
            return None

        return await self.archive(
            messages,
            runtime=runtime,
            session_key=session.key,
            history=history_messages,
            request_tools=tools,
            previous_summary=previous_summary,
            input_token_budget=input_token_budget,
        )


class Consolidator:
    """Coordinate session Memory checkpoints through ``MemoryArchiver``."""

    _SAFETY_BUFFER = 1024  # extra headroom for tokenizer estimation drift

    def __init__(
        self,
        store: MemoryStore,
        sessions: SessionManager,
        build_messages: Callable[..., list[dict[str, Any]]],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        resolve_prompt_context: Callable[[Session], tuple[str | None, Path | None]] | None = None,
        escalate_runtime: Callable[[int, LLMRuntime], LLMRuntime | None] | None = None,
    ):
        self.store = store
        self.sessions = sessions
        self._build_messages = build_messages
        self._get_tool_definitions = get_tool_definitions
        self.archiver = MemoryArchiver(
            store=store,
            build_messages=build_messages,
            get_tool_definitions=get_tool_definitions,
            resolve_prompt_context=resolve_prompt_context,
            escalate_runtime=escalate_runtime,
        )
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    def get_lock(self, session_key: str) -> asyncio.Lock:
        """Return the shared consolidation lock for one session."""
        return self._locks.setdefault(session_key, asyncio.Lock())

    async def summarize_transcript(
        self,
        accepted_messages: list[dict[str, Any]],
        previous_summary: str | None,
        *,
        runtime: LLMRuntime,
        session_key: str,
        tools: list[dict[str, Any]],
        provider_state: ProviderConversationState | None = None,
    ) -> str | None:
        """Summarize the exact transcript prefix already accepted by the model."""
        source_messages = [
            dict(message)
            for message in accepted_messages
            if message.get("role") != "system"
        ]
        if not source_messages:
            return None

        max_output_tokens = max(0, runtime.generation.max_tokens)
        input_token_budget = runtime.context_window_tokens - max_output_tokens
        checkpoint_tokens = min(
            max_output_tokens,
            max(1, (input_token_budget - self._SAFETY_BUFFER) // 2),
        )

        summary = await self.archiver.archive(
            source_messages,
            runtime=runtime,
            session_key=session_key,
            history=accepted_messages,
            request_tools=tools,
            previous_summary=previous_summary,
            input_token_budget=input_token_budget,
            fallback_max_tokens=max(1, checkpoint_tokens),
            provider_state=provider_state,
        )
        if summary is None:
            return None
        return summary

    async def summarize_provider_compaction(
        self,
        state: ProviderConversationState,
        fallback_messages: list[dict[str, Any]],
        previous_summary: str | None,
        *,
        runtime: LLMRuntime,
        session_key: str,
        tools: list[dict[str, Any]],
    ) -> str | None:
        """Prompt a native compacted state without replaying its raw history."""
        return await self.summarize_transcript(
            fallback_messages,
            previous_summary,
            runtime=runtime,
            session_key=session_key,
            tools=tools,
            provider_state=state,
        )

    @staticmethod
    def _full_replay_history(
        session: Session,
    ) -> list[dict[str, Any]]:
        """Return all messages that can reach the next model prompt."""
        if not session.messages:
            return []
        return session.get_history()

    def estimate_session_prompt_tokens(
        self,
        session: Session,
        *,
        runtime: LLMRuntime,
    ) -> tuple[int, str]:
        """Estimate prompt size from the full replayable session history."""
        history = self._full_replay_history(session)
        channel = session.key.split(":", 1)[0] if ":" in session.key else None
        summary = session_summary_from_metadata(
            session.metadata,
            fallback_last_active=session.updated_at,
        )
        probe_messages = self._build_messages(
            history=history,
            current_message="[token-probe]",
            channel=channel,
            session_summary=summary,
        )
        return estimate_prompt_tokens_chain(
            runtime.provider,
            runtime.model,
            probe_messages,
            self._get_tool_definitions(),
        )

    def _input_token_budget(self, runtime: LLMRuntime) -> int:
        """Available input token budget for consolidation LLM."""
        return (
            runtime.context_window_tokens
            - runtime.generation.max_tokens
            - self._SAFETY_BUFFER
        )

    async def archive_session(
        self,
        session: Session,
        *,
        archive_end: int,
        runtime: LLMRuntime,
    ) -> str | None:
        """Archive one captured session range through the shared Memory path."""
        return await self.archiver.archive_session(
            session,
            archive_end=archive_end,
            runtime=runtime,
            input_token_budget=self._input_token_budget(runtime),
        )

    async def compact_idle_session(
        self,
        session_key: str,
        *,
        runtime: LLMRuntime,
        max_suffix: int = 0,
        events: EventSink = NO_EVENTS,
    ) -> str | None:
        """Replace archived history with a summary checkpoint.

        ``max_suffix`` is accepted for SDK compatibility and no longer retains
        archived messages. All compaction triggers share checkpoint replay.
        """
        lock = self.get_lock(session_key)
        async with lock:
            self.sessions.invalidate(session_key)
            session = self.sessions.get_or_create(session_key)

            archive_start = session.last_archived
            messages_to_archive = list(session.messages[archive_start:])
            has_new_messages = any(
                not message.get("_command") and not is_summary_checkpoint(message)
                for message in messages_to_archive
            )
            if not has_new_messages:
                return ""

            compaction_id = uuid4().hex
            await events.emit(
                ContextCompactionEvent(compaction_id=compaction_id, phase="started"),
            )
            last_active = session.updated_at
            archive_end = archive_start + len(messages_to_archive)

            # Pre-flight: prove restorability before the pass may run. The law
            # (Evans 2026-09-24): a valid compressed context commits, or
            # NOTHING happens and the error is reported. No restore guarantee,
            # no compaction.
            session_path = self.sessions._get_session_path(session_key)
            backup_bytes: bytes | None = None
            try:
                self.sessions.save(session)
                backup_bytes = session_path.read_bytes()
                backup_path = session_path.parent / (
                    session_path.name + f".pre-compact-{datetime.now():%Y%m%d}.bak"
                )
                backup_path.write_bytes(backup_bytes)
                if backup_path.read_bytes() != backup_bytes:
                    raise OSError("backup verification failed")
            except Exception:
                logger.exception(
                    "Compaction pre-flight could not guarantee restorability for {}; refusing",
                    session_key,
                )
                await events.emit(
                    ContextCompactionEvent(compaction_id=compaction_id, phase="failed"),
                )
                return None

            try:
                summary = await self.archive_session(
                    session, archive_end=archive_end, runtime=runtime,
                )
                if summary:
                    # Concurrent appends remain after the captured boundary.
                    session.commit_summary_checkpoint(
                        summary, insert_at=archive_end, last_active=last_active,
                    )
                    # Resume from the summary and retained transcript, not the old provider history.
                    session.provider_state = None
                    self.sessions.save(session)
            except (Exception, asyncio.CancelledError) as exc:
                if backup_bytes is not None:
                    try:
                        session_path.write_bytes(backup_bytes)
                        self.sessions.invalidate(session_key)
                        logger.warning(
                            "Compaction failed for {}; restored the pre-compaction state",
                            session_key,
                        )
                    except Exception:
                        logger.exception(
                            "Compaction restore failed for {} — MANUAL INTERVENTION",
                            session_key,
                        )
                await events.emit(
                    ContextCompactionEvent(
                        compaction_id=compaction_id,
                        phase="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                    ),
                )
                raise
            if not summary:
                await events.emit(
                    ContextCompactionEvent(compaction_id=compaction_id, phase="failed"),
                )
                return None

            await events.emit(
                ContextCompactionEvent(
                    compaction_id=compaction_id,
                    phase="succeeded",
                ),
            )

            logger.info(
                "Idle-session compact for {}: archived={}, visible={}, retained={}, summary={}",
                session_key,
                len(messages_to_archive),
                len(session.get_history()),
                len(session.messages),
                bool(summary),
            )

            return summary
