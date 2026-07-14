"""Reviewers for phases-oss.

* ``local`` -- the default static, regex-based, offline reviewer.
* ``cloud`` -- an opt-in shell that calls an external reviewer only when the
  user wires a sender (added behind the data gate).

Use :func:`get_reviewer` to obtain one by name.
"""

from .base import Finding, Severity, findings_to_verdict
from .local import DEFAULT_RULES, LocalReviewer, Rule, get_reviewer, scan_text

__all__ = [
    "Finding",
    "Severity",
    "findings_to_verdict",
    "DEFAULT_RULES",
    "LocalReviewer",
    "Rule",
    "get_reviewer",
    "scan_text",
]
