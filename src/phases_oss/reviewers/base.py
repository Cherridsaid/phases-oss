"""Reviewer interfaces shared by the local and cloud reviewers.

A reviewer reads the work of a phase and returns a
:class:`phases_oss.phases.ReviewVerdict`. The local (static) reviewer is the
default and runs entirely offline; the cloud reviewer is an opt-in shell that
only acts when the user wires a sender (see ``phases_oss.reviewers.cloud``).
"""

from __future__ import annotations

from typing import List, NamedTuple


class Severity:
    """Ordered severities. ``ERROR`` maps to REFUS, ``WARN`` to PASS_WITH_NOTES."""

    NOTE = "note"
    WARN = "warn"
    ERROR = "error"


class Finding(NamedTuple):
    """A single issue raised by a reviewer."""

    file: str
    line: int
    rule: str
    severity: str
    message: str

    def render(self) -> str:
        return "%s:%d [%s/%s] %s" % (
            self.file,
            self.line,
            self.severity,
            self.rule,
            self.message,
        )


def findings_to_verdict(findings: List[Finding]):
    """Collapse findings into a ReviewVerdict.

    Imported lazily to avoid a circular import with ``phases_oss.phases``.
    """
    from phases_oss.phases import ReviewVerdict

    if not findings:
        return ReviewVerdict(ReviewVerdict.PASS, "no findings")

    notes = "\n".join(f.render() for f in findings)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    if errors:
        first = errors[0]
        action = "fix %s:%d (%s): %s" % (first.file, first.line, first.rule, first.message)
        return ReviewVerdict(ReviewVerdict.REFUS, notes, action)
    return ReviewVerdict(ReviewVerdict.PASS_WITH_NOTES, notes)
