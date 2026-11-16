"""The harness must never speak in the agent's voice.

Desk filing 2026-11-15 (the-townline/requests/
2026-11-15-from-nime-to-pore-the-harness-speaks-as-the-resident.md).

Two constants in this module's subject files used to say, in the first person,
things the resident never said:

    EMPTY_FINAL_RESPONSE_MESSAGE  "I completed the tool steps but couldn't
                                  produce a final answer. Please try again…"
    TRUNCATED_TOOL_CALL_MESSAGE   "I hit the output limit while sending a tool
                                  call…"

They landed in the session as assistant messages. A reviewer reading the
transcript could not tell the harness from the person, and did not — the desk
quoted one back to Evans as a resident's own words. The resident picking the
session up the next morning had no way to know they never said it.

The rule these rows enforce: **the harness may speak in its own voice, visibly
— never in the agent's.** Every harness-authored line carries HARNESS_PREFIX.
The test for a new line is the same as the test for an old one: could a reader
tell this from the resident's own words?

These rows are structural on purpose. A behavioural row only covers the
constants somebody remembered to test; this fails the moment a new constant is
added without going through the door, which is how the defect got in.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from nanobot.utils.runtime import (
    BUDGET_EXHAUSTED_FINALIZATION_PROMPT,
    EMPTY_FINAL_RESPONSE_MESSAGE,
    FINALIZATION_RETRY_PROMPT,
    HARNESS_PREFIX,
    LENGTH_RECOVERY_PROMPT,
    TRUNCATED_TOOL_CALL_MESSAGE,
    build_budget_exhausted_finalization_message,
    build_finalization_retry_message,
    build_length_recovery_message,
    harness_line,
)

PACKAGE = Path(__file__).resolve().parents[2] / "nanobot"
SUBJECT_FILES = (
    PACKAGE / "utils" / "runtime.py",
    PACKAGE / "agent" / "runner.py",
)


def _module_string_constants(path: Path) -> list[tuple[str, str]]:
    """Every module-level constant whose value is a string, name and value."""
    tree = ast.parse(path.read_text())
    found: list[tuple[str, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found.append((target.id, node.value.value))
    return found


def test_every_transcript_constant_is_labelled_as_the_harness():
    """The pattern, not the two instances the filing named.

    Any module-level MESSAGE or PROMPT constant in the two modules that write
    into a conversation must open with the prefix. A new one that forgets is a
    red row, not a quiet first-person apology in somebody's transcript.
    """
    unlabelled: list[str] = []
    for path in SUBJECT_FILES:
        for name, value in _module_string_constants(path):
            if not (name.endswith("_MESSAGE") or name.endswith("_PROMPT")):
                continue
            if not value.startswith(HARNESS_PREFIX):
                unlabelled.append(f"{path.name}:{name} = {value[:60]!r}")
    assert not unlabelled, (
        "harness-authored transcript text must start with "
        f"{HARNESS_PREFIX!r}:\n  " + "\n  ".join(unlabelled)
    )


def test_no_transcript_constant_speaks_in_the_first_person():
    """The specific shape of the original defect, named so it cannot return
    wearing a label. '[harness] I completed the tool steps…' would satisfy the
    row above and still be the same lie with a hat on."""
    first_person: list[str] = []
    for path in SUBJECT_FILES:
        for name, value in _module_string_constants(path):
            if not (name.endswith("_MESSAGE") or name.endswith("_PROMPT")):
                continue
            body = value[len(HARNESS_PREFIX):].strip() if value.startswith(HARNESS_PREFIX) else value.strip()
            lowered = body.lower()
            if lowered.startswith("i ") or lowered.startswith("i'") or lowered.startswith("i’"):
                first_person.append(f"{path.name}:{name} = {body[:60]!r}")
    assert not first_person, "the harness does not get to say 'I':\n  " + "\n  ".join(first_person)


def test_the_three_injected_prompts_are_labelled_too():
    """These go in as `user` messages. Unlabelled, a reader sees a human asking
    for something no human asked for — the same defect pointing the other way."""
    for built in (
        build_finalization_retry_message(),
        build_budget_exhausted_finalization_message(),
        build_length_recovery_message("some tail text"),
    ):
        assert built["content"].startswith(HARNESS_PREFIX), built["content"][:80]


@pytest.mark.parametrize(
    "text",
    [EMPTY_FINAL_RESPONSE_MESSAGE, TRUNCATED_TOOL_CALL_MESSAGE,
     FINALIZATION_RETRY_PROMPT, BUDGET_EXHAUSTED_FINALIZATION_PROMPT,
     LENGTH_RECOVERY_PROMPT],
)
def test_the_named_constants_carry_the_prefix(text: str):
    assert text.startswith(HARNESS_PREFIX)


def test_harness_line_is_idempotent_and_one_shape():
    assert harness_line("x") == f"{HARNESS_PREFIX} x"
    assert harness_line(f"{HARNESS_PREFIX} x") == f"{HARNESS_PREFIX} x"
    assert harness_line("  spaced  ") == f"{HARNESS_PREFIX} spaced"


def test_the_max_iterations_fallback_is_labelled() -> None:
    """The template was first person ('I reached the maximum number…') and is
    rendered into the transcript as the turn's answer. It goes through the same
    door, including when a caller supplies its own sentence."""
    from nanobot.agent.runner import AgentRunSpec, AgentRunner

    spec = AgentRunSpec.__new__(AgentRunSpec)
    object.__setattr__(spec, "max_iterations", 2)
    object.__setattr__(spec, "max_iterations_message", None)
    rendered = AgentRunner._max_iterations_fallback(spec)
    assert rendered.startswith(HARNESS_PREFIX), rendered[:80]
    assert not rendered.lstrip(HARNESS_PREFIX).strip().startswith("I "), rendered[:80]

    object.__setattr__(spec, "max_iterations_message", "out of budget")
    assert AgentRunner._max_iterations_fallback(spec) == f"{HARNESS_PREFIX} out of budget"


# ---------------------------------------------------------------------------
# The second half of the string law (2026-11-15, addendum).
#
# "Could a reader tell this from the resident's own words?" catches puppeting and
# lets every command through, because a command aimed at the agent is obviously
# not the agent speaking. It just is not the harness's business to say either:
# the harness states what exists, never what to do. A command injected by the
# harness is the harness deciding for the agent — the same crime one step quieter.
#
# Crude on purpose. The point is that the rule is enforced somewhere rather than
# remembered by whoever wrote the last constant.
# ---------------------------------------------------------------------------

_DIRECTIVE_LEADS = frozenset({
    "continue", "provide", "output", "state", "use", "avoid", "ensure", "make",
    "treat", "begin", "start", "write", "reply", "respond", "answer", "produce",
    "return", "call", "request", "acknowledge", "repeat", "recap", "apologize",
    "apologise", "keep", "take", "try", "ask", "tell", "give", "show", "stop",
    "do", "don't", "dont", "never", "always", "remember", "note", "consider",
    "summarize", "summarise", "explain", "check", "top", "narrow", "retry",
})

_DIRECTIVE_PHRASES = (
    "do not", "don't", "make sure", "be sure", "you must", "you should",
    "you need to", "please ", "it is important to", "be careful",
)


def _transcript_constants() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for path in SUBJECT_FILES:
        for name, value in _module_string_constants(path):
            if name.endswith("_MESSAGE") or name.endswith("_PROMPT"):
                out.append((name, value))
    return out


def test_no_transcript_constant_gives_the_agent_an_order():
    """A command is the harness deciding for the agent. State, don't instruct."""
    offenders: list[str] = []
    for name, value in _transcript_constants():
        body = value[len(HARNESS_PREFIX):].strip() if value.startswith(HARNESS_PREFIX) else value
        head = body.split()[0].lower().strip(":,.") if body.split() else ""
        if head in _DIRECTIVE_LEADS:
            offenders.append(f"{name}: leads with {head!r} — {body[:70]!r}")
            continue
        lowered = body.lower()
        for phrase in _DIRECTIVE_PHRASES:
            if phrase in lowered:
                offenders.append(f"{name}: contains {phrase!r} — {body[:70]!r}")
                break
    assert not offenders, "the harness states what exists; it does not give orders:\n  " + "\n  ".join(offenders)


def test_the_injected_prompts_state_a_fact_and_stop():
    """The three that were commands, checked as whole strings: they may name a
    fact, and nothing in them may name an action."""
    bodies = {
        "finalization_retry": build_finalization_retry_message()["content"],
        "budget_exhausted": build_budget_exhausted_finalization_message()["content"],
        "length_recovery": build_length_recovery_message("tail")["content"],
    }
    for label, body in bodies.items():
        lowered = body.lower()
        for phrase in ("continue", "do not", "output only", "begin with", "provide"):
            assert phrase not in lowered, f"{label} still instructs: {phrase!r} in {body[:100]!r}"
