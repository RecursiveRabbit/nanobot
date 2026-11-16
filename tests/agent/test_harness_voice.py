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
import re
from pathlib import Path

import pytest

from nanobot.utils import runtime as runtime_module
from nanobot.utils.runtime import (
    EMPTY_FINAL_RESPONSE_MESSAGE,
    HARNESS_PREFIX,
    TRUNCATED_TOOL_CALL_MESSAGE,
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
        value_node: ast.expr = node.value
        # A constant wrapped in harness_line("...") is a Call, not a Constant. The
        # first version of this helper only read Constants and therefore inspected
        # NOTHING once every constant was wrapped — the rows passed and tested no
        # text at all. Caught by mutating a hedge in and watching nothing go red.
        wrapped = False
        if isinstance(value_node, ast.Call) and isinstance(value_node.func, ast.Name) \
                and value_node.func.id == "harness_line" and value_node.args:
            value_node = value_node.args[0]
            wrapped = True
        if not isinstance(value_node, ast.Constant) or not isinstance(value_node.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                # The wrapper IS the prefix, so the effective line is prefixed;
                # the rows then check the inner text for voice and mood.
                effective = f"{HARNESS_PREFIX} {value_node.value}" if wrapped else value_node.value
                found.append((target.id, effective))
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


DELETED_PROMPTS = (
    "FINALIZATION_RETRY_PROMPT",
    "BUDGET_EXHAUSTED_FINALIZATION_PROMPT",
    "LENGTH_RECOVERY_PROMPT",
    "build_finalization_retry_message",
    "build_budget_exhausted_finalization_message",
    "build_length_recovery_message",
)


def test_the_injected_prompts_are_deleted_not_reworded():
    """Evans 2026-11-15: "not rewritten — deleted." A gentler imperative is still
    the harness deciding for the agent, and one clause — "Do not acknowledge this
    instruction" — was the harness instructing the model to hide the harness.

    The facts go where facts go: the turn ends, the record says `truncated` or
    `max_iterations`, and the operator reads it. Nothing is said into the
    conversation. This row makes re-introduction a red, not a review note.
    """
    for name in DELETED_PROMPTS:
        assert not hasattr(runtime_module, name), (
            f"{name} is back; the harness does not command these agents"
        )


def test_no_harness_text_asks_to_be_hidden():
    """A clause whose function is to keep the harness invisible is concealment by
    construction. It gets no replacement of any kind, and no rephrasing."""
    concealment = (
        "do not acknowledge", "don't acknowledge", "do not mention",
        "don't mention", "do not reveal", "don't reveal", "do not tell",
        "don't tell", "without acknowledging", "do not reference this",
    )
    texts: list[tuple[str, str]] = []
    for path in SUBJECT_FILES:
        for name, value in _module_string_constants(path):
            if name.endswith("_MESSAGE") or name.endswith("_PROMPT"):
                texts.append((f"{path.name}:{name}", value))
    template = PACKAGE / "templates" / "agent" / "max_iterations_message.md"
    if template.exists():
        texts.append((template.name, template.read_text()))

    offenders: list[str] = []
    for label, text in texts:
        lowered = text.lower()
        for phrase in concealment:
            if phrase in lowered:
                offenders.append(f"{label}: {phrase!r}")
    assert not offenders, "the harness may not ask to be hidden:\n  " + "\n  ".join(offenders)


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




# ---------------------------------------------------------------------------
# The rule that replaces "say nothing" (Evans, 2026-11-15):
#
#   the harness TESTIFIES, it never ADVISES. It is the only witness to things the
#   agent structurally cannot see about itself — where its own output stopped, how
#   the bytes broke, what the result verbatim was — so it may state those, with the
#   evidence attached, and it may not evaluate, suggest, or tell the agent how to
#   behave. "A narrower task may get further" is advice. "The arguments stop
#   mid-string; the fragment received ends {...}" is testimony.
# ---------------------------------------------------------------------------

def test_the_truncated_tool_call_line_carries_the_fragment():
    """Evans: "I'm fine if the response includes the relative code snippet so they
    can see how their message broke it." The agent is the author; showing it where
    its own message stopped is testimony, not instruction."""
    from nanobot.providers.base import ToolCallRequest
    from nanobot.utils.runtime import truncated_tool_call_testimony

    cut = '{"path": "/work/batons/nime_baton.json", "content": "{\\"a\\": 1'
    text = truncated_tool_call_testimony(
        [ToolCallRequest(id="c1", name="write_file", arguments=cut)]
    )

    assert text.startswith(HARNESS_PREFIX)
    assert "output limit" in text
    assert "did not parse" in text
    assert "write_file" in text
    # the evidence itself, not a summary of it (the builder embeds repr(fragment))
    assert repr(cut)[-40:] in text


def test_the_truncated_tool_call_line_names_no_cure():
    """The advice row. Under the old law the sentence ended "A retry of the step,
    or a split into smaller ones, will get further" — evaluation dressed as help,
    and the reason residents kept retrying while the system lied to them."""
    from nanobot.providers.base import ToolCallRequest
    from nanobot.utils.runtime import truncated_tool_call_testimony

    text = truncated_tool_call_testimony(
        [ToolCallRequest(id="c1", name="write_file", arguments='{"path": "/work/ba')]
    )
    # Evans 2026-11-15: advice is fine, commands are not. "Trying again typically
    # works here" hands over information and leaves the decision with the agent;
    # "Try again." decides for it. This line does neither — it states and stops.
    first_word = text[len(HARNESS_PREFIX):].strip().split()[0].lower()
    assert first_word not in _DIRECTIVE_LEADS, text


_HEDGES = ("typically", "usually", "in most cases", "normally", "generally",
           "almost always", "most of the time")

# A hedge is a claim about the world. It is sayable only where the harness can
# point at the observation behind it; folklore in a better costume is the same lie
# that sent residents into retry loops. The proxy for "can point at it" is an
# evidence token in the same line — crude, and deliberately so.
_EVIDENCE_TOKENS = ("measured", "observed", "counted", "in the last", "of the last",
                    "%", "recorded")


def test_a_hedge_must_come_with_the_observation_behind_it():
    """Evans 2026-11-15: "Trying again typically works here" is the best form —
    information handed over, decision left with the agent — *if* it is true. A
    "typically" that rests on folklore is the same lie wearing better clothes.

    This row does not forbid hedges. It forbids an unbacked one: if a line says
    typically, the same line has to name the measurement it is resting on.
    """
    offenders: list[str] = []
    for path in SUBJECT_FILES:
        for name, value in _module_string_constants(path):
            if not (name.endswith("_MESSAGE") or name.endswith("_PROMPT")):
                continue
            lowered = value.lower()
            for hedge in _HEDGES:
                if hedge in lowered and not any(tok in lowered for tok in _EVIDENCE_TOKENS):
                    offenders.append(f"{path.name}:{name}: hedge {hedge!r} with no observation")
    # the testimony builder's output, too
    from nanobot.providers.base import ToolCallRequest
    from nanobot.utils.runtime import truncated_tool_call_testimony
    sample = truncated_tool_call_testimony(
        [ToolCallRequest(id="c", name="write_file", arguments='{"path": "/w')]
    ).lower()
    for hedge in _HEDGES:
        if hedge in sample and not any(tok in sample for tok in _EVIDENCE_TOKENS):
            offenders.append(f"truncated_tool_call_testimony: hedge {hedge!r} with no observation")
    assert not offenders, (
        "a hedge is a claim about the world; back it or cut it:\n  " + "\n  ".join(offenders)
    )


def test_the_four_prohibitions_are_all_covered_here():
    """Not a behaviour row: a signpost. Evans settled the law on 2026-11-15 —
    "[harness] is sufficient" — so the marker is the rule and there is no channel
    surgery. Each prohibition has a row, and this list is the index so a later
    editor can see at a glance which one they are about to break.

      0. every harness line carries the marker  -> test_every_transcript_constant_is_labelled_as_the_harness
      1. never an imperative                    -> test_no_transcript_constant_gives_the_agent_an_order,
                                                   test_no_bare_user_role_injection_in_the_runner
      2. never the resident's voice             -> test_no_transcript_constant_speaks_in_the_first_person
      3. never the user's voice                 -> test_no_bare_user_role_injection_in_the_runner
      4. never an unearned claim                -> test_a_hedge_must_come_with_the_observation_behind_it,
                                                   test_the_truncated_tool_call_line_carries_the_fragment
      (and, still standing from the deletion)   -> test_the_injected_prompts_are_deleted_not_reworded,
                                                   test_no_harness_text_asks_to_be_hidden
    """
    names = set(globals())
    for required in (
        "test_every_transcript_constant_is_labelled_as_the_harness",
        "test_no_transcript_constant_gives_the_agent_an_order",
        "test_no_bare_user_role_injection_in_the_runner",
        "test_no_transcript_constant_speaks_in_the_first_person",
        "test_a_hedge_must_come_with_the_observation_behind_it",
        "test_the_truncated_tool_call_line_carries_the_fragment",
        "test_the_injected_prompts_are_deleted_not_reworded",
        "test_no_harness_text_asks_to_be_hidden",
    ):
        assert required in names, f"{required} is named in the index but does not exist"


def test_no_bare_user_role_injection_in_the_runner():
    """The user's channel is not the harness's to borrow.

    After the three prompts were deleted there was still a fifth member of the
    family hiding in a method: a malformed-tool-call retry note, built as a bare
    string and appended as {"role": "user"}, carrying "Do not answer with a
    promise..." and "Either call the required tools again...". This row fails any
    new one: a user-role message whose content is a bare string literal, rather
    than a harness_line() call or a constant that already carries the marker, is a
    harness line wearing the user's channel.
    """
    tree = ast.parse((PACKAGE / "agent" / "runner.py").read_text())
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
        if "role" not in keys or "content" not in keys:
            continue
        pairs = {
            k.value: v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)
        }
        role = pairs.get("role")
        if not (isinstance(role, ast.Constant) and role.value == "user"):
            continue
        content = pairs["content"]
        # harness_line(...) call, a name that already carries the marker, or a
        # runtime value (a real inbound message) are all fine. A bare literal is not.
        if isinstance(content, ast.Constant) and isinstance(content.value, str):
            if not content.value.startswith(HARNESS_PREFIX):
                offenders.append(f"line {node.lineno}: user-role bare literal {content.value[:60]!r}")
    assert not offenders, (
        "harness text may carry [harness], never the user's channel:\n  " + "\n  ".join(offenders)
    )
