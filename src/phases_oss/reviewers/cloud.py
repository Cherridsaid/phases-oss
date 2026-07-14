"""Opt-in cloud reviewer (a shell, not a client).

This module deliberately performs **no network I/O of its own**. It imports no
HTTP library. The only way bytes leave the machine is through a *sender* that
the user wires explicitly:

    sender(payload_text: str, destination: str) -> str

If no sender is wired, the cloud reviewer is **inert**: nothing is ever sent.

When a sender *is* wired, every payload is forced through a
:class:`phases_oss.data_gate.DataGate` first:

1. the destination host must be on the gate's allowlist (deny by default) --
   otherwise the reviewer skips and the sender is never called;
2. the payload is redacted (secrets, identity, paths) before it reaches the
   sender, and the disclosure is attached to the verdict notes.

The reviewer **fails closed on unavailability**: an absent backend, an
unreachable sender, an empty reply or an unparseable reply all yield
``REVIEW_UNAVAILABLE`` -- never a PASS. A review that did not actually happen
cannot approve anything; there is no fourth outcome besides PASS / REFUS /
REVIEW_UNAVAILABLE. Deterministic tests remain the authority on "does it
work"; the reviewer is an independent quality check on top.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from phases_oss.data_gate import DataGate, GateError
from phases_oss.phases import Phase, ReviewVerdict

# A sender is any callable the user supplies. It is the ONLY outbound path.
Sender = Callable[[str, str], str]
PromptBuilder = Callable[[Phase], str]

_MAX_FILE_BYTES = 100_000


def default_build_prompt(phase: Phase, root: Optional[Path] = None) -> str:
    """Build a review prompt from the phase's objective and file contents.

    Relative ``files_allowed`` entries are resolved against ``root`` (the repo
    root) when given -- resolving against the process cwd would silently produce
    an empty prompt whenever the reviewer runs from another directory. The
    returned text is what would be sent -- it always passes through the data
    gate's redaction before any sender sees it.
    """
    parts = [
        "Review the following change for correctness and risk.",
        "Objective: %s" % phase.objective,
        "Reply with a line 'VERDICT: PASS' or 'VERDICT: REFUS', then notes.",
        "",
    ]
    for rel in phase.files_allowed:
        p = Path(rel)
        if root is not None and not p.is_absolute():
            p = Path(root) / rel
        if not p.exists() or p.is_dir():
            continue
        try:
            text = p.read_text(encoding="utf-8")[:_MAX_FILE_BYTES]
        except (UnicodeDecodeError, OSError):
            continue
        parts.append("--- %s ---" % rel)
        parts.append(text)
    return "\n".join(parts)


def parse_response(raw: str) -> ReviewVerdict:
    """Map an external reviewer's free-text reply to a verdict (fail closed).

    An explicit REFUS/EXPLOITED anywhere produces a REFUS (erring toward
    refusal is safe). A pass requires the explicit marker ``VERDICT: PASS`` --
    a stray "pass" inside prose ("I cannot pass judgment") must not approve.
    Anything else -- an empty or garbled reply -- is ``REVIEW_UNAVAILABLE``:
    the review did not verifiably happen, so it can neither approve nor be
    silently skipped.
    """
    if not raw or not raw.strip():
        return ReviewVerdict(ReviewVerdict.UNAVAILABLE, "cloud reviewer: empty response")
    upper = raw.upper()
    if "REFUS" in upper or "EXPLOITED" in upper:
        return ReviewVerdict(ReviewVerdict.REFUS, raw.strip(), action="see cloud reviewer notes")
    if "VERDICT: PASS" in upper:
        return ReviewVerdict(ReviewVerdict.PASS_WITH_NOTES, raw.strip())
    return ReviewVerdict(
        ReviewVerdict.UNAVAILABLE,
        "cloud reviewer: unrecognized response\n" + raw.strip(),
    )


class CloudReviewer:
    """Opt-in reviewer that delegates to a user-supplied sender, behind the gate."""

    name = "cloud"

    def __init__(
        self,
        sender: Optional[Sender] = None,
        gate: Optional[DataGate] = None,
        endpoint: str = "",
        build_prompt: Optional[PromptBuilder] = None,
        root: Optional[Path] = None,
    ):
        self.sender = sender
        # No gate provided => empty allowlist => deny all (safe default).
        self.gate = gate if gate is not None else DataGate()
        self.endpoint = endpoint
        self.root = Path(root) if root is not None else None
        # The default builder resolves relative files against ``root`` (same
        # contract as LocalReviewer); a custom builder owns its own resolution.
        self.build_prompt = build_prompt or (
            lambda phase: default_build_prompt(phase, self.root)
        )

    def is_configured(self) -> bool:
        """True only if a sender AND an endpoint are wired."""
        return self.sender is not None and bool(self.endpoint and self.endpoint.strip())

    def review(self, phase: Phase) -> ReviewVerdict:
        if not self.is_configured():
            # Backend absent = the review cannot happen. Fail closed, never PASS.
            return ReviewVerdict(
                ReviewVerdict.UNAVAILABLE,
                "cloud reviewer not configured (no sender/endpoint); no network performed",
            )

        prompt = self.build_prompt(phase)
        try:
            payload = self.gate.prepare(prompt, self.endpoint)
        except GateError as exc:
            # Destination not allowlisted -> never call the sender. The review
            # did not happen, so it cannot count as one.
            return ReviewVerdict(
                ReviewVerdict.UNAVAILABLE,
                "cloud reviewer blocked by the data gate: %s" % exc,
            )

        # The single outbound call. The sender only ever sees redacted text.
        try:
            raw = self.sender(payload.text, payload.destination)
        except Exception as exc:  # noqa: BLE001 -- unreachable = unavailable, never PASS
            return ReviewVerdict(
                ReviewVerdict.UNAVAILABLE,
                "cloud reviewer unreachable: %s" % exc,
            )

        verdict = parse_response(raw)
        # Surface what was disclosed so the user can audit it.
        verdict.notes = (payload.disclosure + "\n\n" + verdict.notes).strip()
        return verdict
