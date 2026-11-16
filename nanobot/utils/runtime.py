"""Runtime-specific helper functions and constants."""

from __future__ import annotations

from nanobot.utils.strings import register_literal, text as string_text

import re
from pathlib import Path
from typing import Any, cast

from loguru import logger

from nanobot.utils.helpers import stringify_text_blocks

_MAX_REPEAT_EXTERNAL_LOOKUPS = 2

# Third same-target workspace violation in a turn escalates to "stop retrying".
_MAX_REPEAT_WORKSPACE_VIOLATIONS = 2
_LENGTH_RECOVERY_TAIL_CHARS = 64

# ---------------------------------------------------------------------------
# The harness's own voice (2026-11-15, desk filing "the harness speaks as the
# resident").
#
# Everything the harness puts into a conversation must be VISIBLY the harness's.
# A reader of a transcript — including the resident picking it up the next
# morning — has to be able to tell a manufactured line from the person's own
# words. First person is never ours to use: an earlier version of these
# constants said "I completed the tool steps but couldn't produce a final
# answer", and the desk quoted one back to Evans as a resident's own sentence.
#
# So: the harness may speak in its own voice, visibly. One prefix, every line,
# enforced by tests/agent/test_harness_voice.py — a new constant that does not
# go through here fails a row rather than quietly wearing somebody's face.
# ---------------------------------------------------------------------------
HARNESS_PREFIX = "[harness]"


def harness_line(text: str) -> str:
    """The only sanctioned shape for harness-authored text in a transcript."""
    body = text.strip()
    if body.startswith(HARNESS_PREFIX):
        return body
    return f"{HARNESS_PREFIX} {body}"


EMPTY_FINAL_RESPONSE_MESSAGE = harness_line(
    "The turn ended without a final answer: the tool steps completed, but no answer "
    "was produced. A narrower task may get further."
)

# The other way a turn can end with nothing to say (2026-11-15). EMPTY above is a
# statement about the model; this one is a statement about the cap, because the two
# are not the same failure and only one of them is worth retrying unchanged.
TRUNCATED_TOOL_CALL_MESSAGE = harness_line(
    "The output limit was reached while sending a tool call, and the call was cut off "
    "before it ran — nothing from it was executed. A retry of the step, or a split into "
    "smaller ones, will get further."
)

# The three below were commands (2026-11-15, second half of the string law):
# "Continue the same response from its exact endpoint", "provide a concise final
# response", "Do not call or request tools". A prefix makes a command visible but
# does not make it the harness's business: the harness states what exists, never
# what to do. Each one is now the fact, with nothing after it — the agent decides,
# which is a decision it can only make if the sentence stops where the fact does.
FINALIZATION_RETRY_PROMPT = harness_line(
    "The previous response contained no text. This is a repeat request."
)

BUDGET_EXHAUSTED_FINALIZATION_PROMPT = harness_line(
    "The tool-call budget for this turn is exhausted. No tools are available for "
    "the rest of this turn."
)

LENGTH_RECOVERY_PROMPT = harness_line(
    "The previous response ended at the output limit."
)

_EMPTY_TOOL_RESULT_DEFAULT = register_literal(
    "literal:empty_tool_result",
    "({tool_name} completed with no output)",
    group="Turns",
)


def empty_tool_result_message(tool_name: str) -> str:
    """Short prompt-safe marker for tools that completed without visible output."""
    template = string_text("literal:empty_tool_result", _EMPTY_TOOL_RESULT_DEFAULT)
    return template.replace("{tool_name}", tool_name)


def ensure_nonempty_tool_result(tool_name: str, content: Any) -> Any:
    """Replace semantically empty tool results with a short marker string."""
    if content is None:
        return empty_tool_result_message(tool_name)
    if isinstance(content, str) and not content.strip():
        return empty_tool_result_message(tool_name)
    if isinstance(content, list):
        if not content:
            return empty_tool_result_message(tool_name)
        text_payload = stringify_text_blocks(cast(list[Any], content))
        if text_payload is not None and not text_payload.strip():
            return empty_tool_result_message(tool_name)
    return cast(Any, content)


def is_blank_text(content: str | None) -> bool:
    """True when *content* is missing or only whitespace."""
    return content is None or not content.strip()


def build_finalization_retry_message() -> dict[str, str]:
    """State that the previous response was empty. No instruction attached."""
    return {"role": "user", "content": FINALIZATION_RETRY_PROMPT}


def build_budget_exhausted_finalization_message() -> dict[str, str]:
    """State that the turn's tool budget is spent. No instruction attached."""
    return {"role": "user", "content": BUDGET_EXHAUSTED_FINALIZATION_PROMPT}


def build_length_recovery_message(content: str) -> dict[str, str]:
    """Report where the previous response ended, and what had already been said.

    Nothing here tells the model what to do. It states two facts — the response
    ended at the limit, and this tail was already delivered — and stops.
    """
    tail = content[-_LENGTH_RECOVERY_TAIL_CHARS:]
    prompt = (
        f"{LENGTH_RECOVERY_PROMPT}\n\n"
        "The following tail was already delivered to the user:\n"
        "<already_delivered_tail>\n"
        f"{tail}\n"
        "</already_delivered_tail>"
    )
    return {"role": "user", "content": prompt}


def external_lookup_signature(tool_name: str, arguments: Any) -> str | None:
    """Stable signature for repeated external lookups we want to throttle."""
    if not isinstance(arguments, dict):
        return None
    arguments = cast(dict[str, Any], arguments)
    if tool_name == "web_fetch":
        url = str(arguments.get("url") or "").strip()
        if url:
            return f"web_fetch:{url.lower()}"
    if tool_name == "web_search":
        query = str(arguments.get("query") or arguments.get("search_term") or "").strip()
        if query:
            return f"web_search:{query.lower()}"
    return None


def repeated_external_lookup_error(
    tool_name: str,
    arguments: Any,
    seen_counts: dict[str, int],
) -> str | None:
    """Block repeated external lookups after a small retry budget."""
    signature = external_lookup_signature(tool_name, arguments)
    if signature is None:
        return None
    count = seen_counts.get(signature, 0) + 1
    seen_counts[signature] = count
    if count <= _MAX_REPEAT_EXTERNAL_LOOKUPS:
        return None
    logger.warning(
        "Blocking repeated external lookup {} on attempt {}",
        signature[:160],
        count,
    )
    return (
        "Error: repeated external lookup blocked. "
        "Use the results you already have to answer, or try a meaningfully different source."
    )


# Workspace-boundary violations are soft errors, with per-target throttling.

_OUTSIDE_PATH_PATTERN = re.compile(r"(?:^|[\s|>'\"])((?:/[^\s\"'>;|<]+)|(?:~[^\s\"'>;|<]+))")


def workspace_violation_signature(
    tool_name: str,
    arguments: Any,
) -> str | None:
    """Return a stable cross-tool signature for the outside-workspace target."""
    if not isinstance(arguments, dict):
        return None
    arguments = cast(dict[str, Any], arguments)
    for key in ("path", "file_path", "target", "source", "destination"):
        val = arguments.get(key)
        if isinstance(val, str) and val.strip():
            return _normalize_violation_target(val.strip())

    if tool_name in {"exec", "shell"}:
        cmd = str(arguments.get("command") or "").strip()
        if cmd:
            match = _OUTSIDE_PATH_PATTERN.search(cmd)
            if match:
                return _normalize_violation_target(match.group(1))
        cwd = str(arguments.get("working_dir") or "").strip()
        if cwd:
            return _normalize_violation_target(cwd)

    return None


def _normalize_violation_target(raw: str) -> str:
    """Normalize *raw* path so that equivalent spellings collide on the same key."""
    try:
        normalized = Path(raw).expanduser().resolve().as_posix()
    except Exception:
        normalized = raw.replace("\\", "/")
    return f"violation:{normalized}".lower()


def repeated_workspace_violation_error(
    tool_name: str,
    arguments: Any,
    seen_counts: dict[str, int],
) -> str | None:
    """Return an escalated error after repeated bypass attempts."""
    signature = workspace_violation_signature(tool_name, arguments)
    if signature is None:
        return None
    count = seen_counts.get(signature, 0) + 1
    seen_counts[signature] = count
    if count <= _MAX_REPEAT_WORKSPACE_VIOLATIONS:
        return None
    logger.warning(
        "Escalating repeated workspace bypass attempt {} (attempt {})",
        signature[:160],
        count,
    )
    target = signature.split("violation:", 1)[1] if "violation:" in signature else signature
    return (
        "Error: refusing repeated workspace-bypass attempts.\n"
        f"You have tried to access '{target}' (or an equivalent path) "
        f"{count} times in this turn. This is a hard policy boundary -- "
        "switching tools, shell tricks, working_dir overrides, symlinks, "
        "or base64 piping will NOT change the answer. Stop retrying. "
        "If the user genuinely needs this resource, tell them you cannot "
        "access it and ask how they want to proceed (e.g. copy the file "
        "into the workspace, or disable restrict_to_workspace for this run)."
    )
