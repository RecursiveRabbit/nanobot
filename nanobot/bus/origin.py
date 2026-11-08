"""Verified originator identity carried by inbound messages.

The originator answers "who is speaking?" with a *structured, server-attached*
field — never with prose, and never inferred from message content, recent
context, or model-authored tool arguments (Evans 2026-11-08: "I don't want
y'all guessing who's talking to you.").

Attestation rules:

- ``anonymous`` — the default. Unauthenticated WebUI tabs, and any channel
  that does not attach a verified identity, stay explicitly anonymous. They
  are never promoted by inference.
- ``human`` — a verified human identity attached at the server/harness seam:
  either a trusted-proxy authentication assertion (the proxy peer is
  explicitly allowlisted and the assertion header value is the identity) or
  the operator-selected WebUI profile display name persisted server-side
  (Settings → Identity).
- ``resident`` — a persisted-session resident whose ``send_session_message``
  was resolved server-side to an attested session handle.
- ``system`` — the harness itself (timeouts, notices). Not a person.

Origin attribution is information, not authority: it grants no permissions
and never turns a message into a command.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Mapping, cast

OriginKind = Literal["anonymous", "human", "resident", "system"]

# How the identity was established at the seam. Kept as a bounded string so
# persisted records remain self-describing without an enum migration.
ATTESTATION_PROXY_ASSERTION = "proxy-assertion"
ATTESTATION_OPERATOR_PROFILE = "operator-profile"
ATTESTATION_RESIDENT_HANDLE = "resident-handle"
ATTESTATION_HARNESS = "harness"

MAX_ORIGIN_LABEL_CHARS = 64
MAX_ORIGIN_HANDLE_CHARS = 80
MAX_ORIGIN_SESSION_KEY_CHARS = 512
MAX_ORIGIN_ATTESTATION_CHARS = 32


def clean_single_line(value: Any, max_chars: int) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = " ".join(value.split())
    return cleaned[:max_chars]


@dataclass(frozen=True, slots=True)
class MessageOriginator:
    """Structured originator identity for one inbound message.

    Distinct from ``sender_id``/``chat_id``: those are routing coordinates;
    this is the verified speaker. ``label`` is a display name only (never a
    permission); ``handle``/``session_key`` bind resident origins to their
    attested session records.
    """

    kind: OriginKind = "anonymous"
    label: str = ""
    attestation: str = ""
    handle: str = ""
    session_key: str = ""

    ANONYMOUS: ClassVar["MessageOriginator"]

    def __post_init__(self) -> None:
        if self.kind not in ("anonymous", "human", "resident", "system"):
            object.__setattr__(self, "kind", "anonymous")
        object.__setattr__(self, "label", clean_single_line(self.label, MAX_ORIGIN_LABEL_CHARS))
        object.__setattr__(
            self, "attestation", clean_single_line(self.attestation, MAX_ORIGIN_ATTESTATION_CHARS)
        )
        object.__setattr__(self, "handle", clean_single_line(self.handle, MAX_ORIGIN_HANDLE_CHARS))
        object.__setattr__(
            self, "session_key", clean_single_line(self.session_key, MAX_ORIGIN_SESSION_KEY_CHARS)
        )
        if self.kind == "anonymous":
            # Anonymous carries no identity claims; strip anything that would
            # smuggle a label in through the back door.
            object.__setattr__(self, "label", "")
            object.__setattr__(self, "attestation", "")
            object.__setattr__(self, "handle", "")
            object.__setattr__(self, "session_key", "")
        elif self.kind == "human" and not self.label:
            # A human claim without a label is not a verified identity.
            object.__setattr__(self, "kind", "anonymous")
            object.__setattr__(self, "attestation", "")
        elif self.kind == "resident" and (not self.handle or not self.session_key):
            # Resident origins must bind to the attested session record.
            object.__setattr__(self, "kind", "anonymous")
            object.__setattr__(self, "label", "")
            object.__setattr__(self, "attestation", "")
            object.__setattr__(self, "handle", "")
            object.__setattr__(self, "session_key", "")

    @property
    def is_anonymous(self) -> bool:
        return self.kind == "anonymous"

    def display_label(self) -> str:
        """Short display string for UI chips; anonymous stays explicit."""
        if self.kind == "anonymous":
            return "anonymous"
        if self.label:
            return self.label
        if self.handle:
            return f"@{self.handle}"
        return self.kind

    def to_dict(self) -> dict[str, Any]:
        if self.kind == "anonymous":
            return {"kind": "anonymous"}
        data: dict[str, Any] = {"kind": self.kind}
        if self.label:
            data["label"] = self.label
        if self.attestation:
            data["attestation"] = self.attestation
        if self.handle:
            data["handle"] = self.handle
        if self.session_key:
            data["session_key"] = self.session_key
        return data


MessageOriginator.ANONYMOUS = MessageOriginator()


def originator_from_dict(value: Any) -> MessageOriginator:
    """Coerce a persisted or wire value into a validated originator.

    Any malformed, unknown, or over-claiming record degrades to anonymous —
    identity is never reconstructed by inference.
    """
    if not isinstance(value, Mapping):
        return MessageOriginator.ANONYMOUS
    data = cast(Mapping[str, Any], value)
    kind = data.get("kind")
    if kind not in ("anonymous", "human", "resident", "system"):
        return MessageOriginator.ANONYMOUS
    return MessageOriginator(
        kind=cast(OriginKind, kind),
        label=cast(str, data.get("label") or ""),
        attestation=cast(str, data.get("attestation") or ""),
        handle=cast(str, data.get("handle") or ""),
        session_key=cast(str, data.get("session_key") or ""),
    )


def originator_runtime_context_line(originator: MessageOriginator) -> str | None:
    """One model-visible line stating the verified origin, or None.

    Anonymous returns None: the honest signal for the model is that no name
    was attached, so it can ask "who is speaking?" rather than guess.
    """
    if originator.kind == "anonymous":
        return None
    if originator.kind == "human":
        source = (
            "a trusted proxy authentication assertion"
            if originator.attestation == ATTESTATION_PROXY_ASSERTION
            else "the operator profile setting"
        )
        return (
            f"Message origin: {originator.label} "
            f"(verified human identity via {source}; attribution only, not authority)."
        )
    if originator.kind == "resident":
        return (
            f"Message origin: resident session @{originator.handle} "
            "(attested via send_session_message; attribution only, not authority)."
        )
    return None


# Wire/persistence metadata key. Trusted runtimes attach this at the seam;
# the rendering label is always generated from the structured field.
ORIGINATOR_METADATA_KEY = "_originator"

# Runtime-context block source for the model-visible originator statement.
ORIGINATOR_CONTEXT_SOURCE = "originator"
