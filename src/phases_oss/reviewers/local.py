"""Default reviewer: static, regex-based, fully offline.

This reviewer is a *linter for process discipline*, not a security scanner and
not an LLM. It deliberately uses only the standard library and a small set of
transparent regular expressions. There is no model, no network call, and no
Ollama/Qwen/any local LLM anywhere in this path -- by design.

Treat its output as advisory. A REFUS means "a human (or the agent) should look
at this line before closing the phase", not "this code is unsafe".
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence

from .base import Finding, Severity, findings_to_verdict


class Rule(NamedTuple):
    name: str
    pattern: "re.Pattern[str]"
    severity: str
    message: str


def _rule(name: str, pattern: str, severity: str, message: str) -> Rule:
    return Rule(name, re.compile(pattern), severity, message)


# Transparent, conservative defaults. Each is a discipline signal, not a verdict
# on safety. Order does not matter; every rule is checked on every line.
DEFAULT_RULES: List[Rule] = [
    _rule(
        "hardcoded-secret",
        r"(?i)(api[_-]?key|secret|passwd|password|token)\s*[:=]\s*['\"][^'\"]{8,}['\"]",
        Severity.ERROR,
        "looks like a hardcoded secret assigned to a string literal",
    ),
    _rule(
        "debugger",
        r"\b(breakpoint\s*\(|pdb\s*\.\s*set_trace\s*\()",
        Severity.ERROR,
        "debugger breakpoint left in the code",
    ),
    _rule(
        "bare-except",
        r"except\s*:",
        Severity.WARN,
        "bare 'except:' swallows every error, including KeyboardInterrupt",  # phases-oss: allow (rule text, not an occurrence)
    ),
    _rule(
        "shell-true",
        r"shell\s*=\s*True",
        Severity.WARN,
        "subprocess with shell=True; ensure the command is not attacker-controlled",  # phases-oss: allow (rule text, not an occurrence)
    ),
    _rule(
        "eval-exec",
        r"\b(eval|exec)\s*\(",
        Severity.WARN,
        "dynamic eval/exec; prefer an explicit dispatch",
    ),
    _rule(
        "todo",
        r"\b(TODO|FIXME|XXX)\b",  # phases-oss: allow (rule text, not an occurrence)
        Severity.NOTE,
        "unfinished-work marker",
    ),
]

# Lines carrying this marker are skipped (e.g. the rule definitions themselves,
# or an intentional, reviewed exception).
_IGNORE_MARKER = "phases-oss: allow"


def scan_text(text: str, filename: str, rules: Sequence[Rule] = DEFAULT_RULES) -> List[Finding]:
    """Apply rules line by line and return findings."""
    findings: List[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _IGNORE_MARKER in line:
            continue
        for rule in rules:
            if rule.pattern.search(line):
                findings.append(
                    Finding(
                        file=filename,
                        line=lineno,
                        rule=rule.name,
                        severity=rule.severity,
                        message=rule.message,
                    )
                )
    return findings


class LocalReviewer:
    """Static reviewer over a phase's ``files_allowed``."""

    name = "local"

    def __init__(self, rules: Optional[Sequence[Rule]] = None, root: Optional[Path] = None):
        self.rules = list(rules) if rules is not None else list(DEFAULT_RULES)
        self.root = Path(root) if root is not None else None

    def scan_files(self, files: Sequence[str]) -> List[Finding]:
        findings: List[Finding] = []
        for rel in files:
            path = Path(rel)
            if self.root is not None and not path.is_absolute():
                path = self.root / rel
            if not path.exists() or path.is_dir():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            findings.extend(scan_text(text, rel, self.rules))
        return findings

    def review(self, phase):
        """Reviewer callable: scan the phase's files and return a verdict."""
        findings = self.scan_files(phase.files_allowed)
        return findings_to_verdict(findings)


def get_reviewer(name: str = "local", **kwargs):
    """Return a reviewer by name. ``local`` is the only built-in default.

    The cloud reviewer is intentionally not constructed here without an explicit
    opt-in; see ``phases_oss.reviewers.cloud``.
    """
    if name == "local":
        return LocalReviewer(**kwargs)
    if name == "cloud":
        from .cloud import CloudReviewer

        return CloudReviewer(**kwargs)
    raise ValueError("unknown reviewer: %r (known: local, cloud)" % name)
