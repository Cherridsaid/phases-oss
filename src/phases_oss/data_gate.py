"""Data gate: what may leave the machine, and in what shape.

The cloud reviewer is opt-in. Before any text is handed to an external sender,
it passes through a :class:`DataGate` that does two things:

1. **Allowlist (deny by default).** A destination is refused unless its host is
   on an explicit allowlist. An empty allowlist refuses everything.
2. **Redaction (defense in depth).** Even for an allowed destination, the
   payload is scrubbed of common secret and identity markers, and a
   **disclosure** describing what was redacted is produced so the user can give
   informed consent.

Honest scope
------------
Redaction here is *best effort* with transparent regular expressions. It is not
a guarantee: a novel secret format, or a secret split across lines, can slip
through. The gate reduces accidental leakage; it does not make it safe to send
sensitive data to a third party. When in doubt, do not enable a cloud
destination. The local static reviewer remains the default precisely so that
nothing leaves the machine unless the user deliberately opts in.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple
from urllib.parse import urlparse


class GateError(Exception):
    """Raised when a destination is not permitted by the allowlist."""


class Redaction(NamedTuple):
    category: str
    count: int


# Each redactor: (category, compiled pattern, replacement).
# Replacement may be a string or a callable (re.sub semantics).
_Replacement = object  # str | Callable[[re.Match], str]


def _redactor(category: str, pattern: str, replacement, flags: int = 0):
    return (category, re.compile(pattern, flags), replacement)


def _keep_field(match: "re.Match[str]") -> str:
    # Preserve the field name (group 1) but drop the value.
    return "%s=[REDACTED:secret]" % match.group(1)


def _keep_prefix_user(match: "re.Match[str]") -> str:
    # Preserve the structural prefix (drive\Users\ or /home/ etc.), drop the user.
    return "%s[REDACTED:user]" % match.group(1)


# Order matters: multi-line private keys first, then specific tokens, then the
# broad assignment rule, then identity (email / paths) last.
_REDACTORS = [
    _redactor(
        "private-key",
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        "[REDACTED:private-key]",
        re.DOTALL,
    ),
    _redactor("aws-access-key", r"\bAKIA[0-9A-Z]{16}\b", "[REDACTED:aws-key]"),
    _redactor(
        "jwt",
        r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
        "[REDACTED:jwt]",
    ),
    _redactor(
        "github-token",
        r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{16,}\b",
        "[REDACTED:gh-token]",
    ),
    # Vendor token grammars, caught even OUTSIDE an assignment (pasted logs,
    # curl lines, JSON). Anthropic before OpenAI: sk-ant-... must not be
    # half-eaten by the generic sk-... rule.
    _redactor(
        "anthropic-key",
        r"\bsk-ant-[A-Za-z0-9_\-]{8,}",
        "[REDACTED:anthropic-key]",
    ),
    _redactor(
        "openai-key",
        r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{16,}",
        "[REDACTED:openai-key]",
    ),
    _redactor(
        "slack-token",
        r"\bxox[baprs]-[A-Za-z0-9\-]{10,}",
        "[REDACTED:slack-token]",
    ),
    _redactor(
        "stripe-key",
        r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{10,}\b",
        "[REDACTED:stripe-key]",
    ),
    _redactor(
        "google-api-key",
        r"\bAIza[0-9A-Za-z_\-]{16,}\b",
        "[REDACTED:google-key]",
    ),
    _redactor(
        "bearer",
        # Token charset covers base64/base64url so a token containing + / =
        # (e.g. "Bearer ab+cd/ef=") is redacted whole, not truncated at the
        # first non-alphanumeric. Kept to the token grammar (not \S+) so a
        # trailing sentence "bearer of bad news." is not over-redacted.
        r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{8,}",
        "Bearer [REDACTED:token]",
    ),
    _redactor(
        # The sensitive word may sit ANYWHERE in the variable name as a
        # complete _-separated component: the old \b...\b anchors missed every
        # prefixed UPPER_SNAKE env name (AWS_SECRET_ACCESS_KEY=, DB_PASSWORD=,
        # OPENAI_API_KEY: ...), the most common way a secret reaches a log
        # line. Requiring whole components keeps prose words that merely
        # contain a keyword ("secretary:", "tokenizer:") out of the net.
        "secret-assign",
        r"(?i)\b((?:[A-Z0-9]+[_\-])*(?:api[_-]?key|apikey|secret|token|passwd|password|credential)s?(?:[_\-][A-Z0-9]+)*)['\"]?\s*[:=]\s*['\"]?[^\s'\"]{6,}['\"]?",
        _keep_field,
    ),
    _redactor(
        "email",
        r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
        "[REDACTED:email]",
    ),
    _redactor(
        # Drive-anchored so a REST path like /users/123 is never matched.
        # Separator-flexible: handles C:\Users\, C:/Users/, C:\\Users\\ (doubled).
        "win-user-path",
        r"(?i)([A-Za-z]:[\\/]+users[\\/]+)[^\\/\s'\"\[]+",
        _keep_prefix_user,
    ),
    _redactor(
        # UNC: \\server\Users\<user>\...
        "unc-user-path",
        r"(?i)(\\\\[^\\/\s'\"]+[\\/]+users[\\/]+)[^\\/\s'\"\[]+",
        _keep_prefix_user,
    ),
    _redactor(
        # Unix: /home/<user> (any case) and macOS /Users/<user> (capital U only,
        # so a REST /users/ path is left alone). The trailing class excludes '['
        # so this redactor never re-matches a '[REDACTED:user]' placeholder that
        # an earlier path redactor already wrote (which would double-count).
        "unix-home-path",
        r"((?:/home/|/Users/))[^/\s'\"\[]+",
        _keep_prefix_user,
    ),
]


def redact(text: str, redactors=_REDACTORS) -> Tuple[str, List[Redaction]]:
    """Scrub ``text`` and report what was redacted.

    Returns the redacted text and a list of :class:`Redaction` (category,
    count). Counts reflect how many substitutions each category made.
    """
    redactions: List[Redaction] = []
    for category, pattern, replacement in redactors:
        # subn counts replacements so the disclosure is accurate.
        text, n = pattern.subn(replacement, text)
        if n:
            redactions.append(Redaction(category, n))
    return text, redactions


def disclosure(redactions: Sequence[Redaction], destination: str, size: int) -> str:
    """Human-readable summary of what is about to be sent, and what was scrubbed."""
    if redactions:
        parts = ", ".join("%s x%d" % (r.category, r.count) for r in redactions)
        scrubbed = "Redacted before sending: %s." % parts
    else:
        scrubbed = "No known secret/identity markers were found to redact."
    return (
        "Data gate disclosure\n"
        "  destination: %s\n"
        "  payload size: %d chars (after redaction)\n"
        "  %s\n"
        "  Note: redaction is best-effort regex, not a guarantee. Do not send "
        "data you are not willing to disclose to this destination." % (destination, size, scrubbed)
    )


class GatePayload(NamedTuple):
    text: str
    disclosure: str
    destination: str
    redactions: List[Redaction]


class DataGate:
    """Allowlist + redaction in front of any external sender."""

    def __init__(self, allowlist: Optional[Sequence[str]] = None, redactors=_REDACTORS):
        # Hosts are compared case-insensitively. Empty allowlist => deny all.
        self.allowlist = {h.strip().lower() for h in (allowlist or []) if h and h.strip()}
        self.redactors = redactors

    @staticmethod
    def _host_of(destination: str) -> str:
        parsed = urlparse(destination)
        if parsed.hostname:
            # urlparse already resolves user@host correctly (host wins).
            return parsed.hostname.lower()
        # Bare host (no scheme): authority is everything before the first '/'.
        authority = destination.strip().lower().split("/", 1)[0]
        # userinfo (user@host): the real host is after the last '@', never the
        # part before it -- so localhost:8789@evil.com resolves to evil.com.
        if "@" in authority:
            authority = authority.rsplit("@", 1)[1]
        # drop a trailing :port
        return authority.split(":", 1)[0]

    def is_allowed(self, destination: str) -> bool:
        if not destination or not destination.strip():
            return False
        host = self._host_of(destination)
        return bool(host) and host in self.allowlist

    def prepare(self, text: str, destination: str) -> GatePayload:
        """Refuse a disallowed destination; otherwise redact and disclose.

        Redaction runs *before* the allow check has any effect on output, so a
        misconfigured destination never sees raw text.
        """
        redacted, redactions = redact(text, self.redactors)
        if not self.is_allowed(destination):
            raise GateError(
                "destination %r is not on the allowlist (deny by default). "
                "Add its host explicitly to send." % destination
            )
        note = disclosure(redactions, destination, len(redacted))
        return GatePayload(redacted, note, destination, redactions)
