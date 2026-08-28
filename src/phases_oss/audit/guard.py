"""Execution-plane guards: no provider secrets, no mutation of the target.

Three separate guarantees, with three very different strengths. They are kept
apart on purpose, because presenting them as one "sandbox" would overstate the
weakest of them.

1. **Secret denylist** -- strong and testable. Deterministic scanners inherit an
   environment with every model-provider and registry credential removed. This
   is a set operation on a dict; :func:`scrub_env` either dropped the variable
   or it did not, and a test can assert it.

2. **Read-only target** -- strong and testable *after the fact*. The target tree
   is fingerprinted before the run and re-fingerprinted after; any difference is
   a hard failure. It detects mutation rather than preventing it, which is why
   execution mode works on an ephemeral copy instead of the original.

3. **Network isolation** -- weak on this platform, and said so. There is no
   per-process network namespace on Windows, so a child that opens a raw socket
   is not stopped by anything here. :func:`network_policy` reports ``advisory``
   in that case and the run records ``degraded`` rather than claiming an
   isolation it does not have. Proxy variables are set to a closed loopback port,
   which stops well-behaved HTTP clients (requests, urllib, curl) and nothing
   else. Writing "network = NONE" without a namespace would be the exact kind of
   unproven claim this project refuses to make.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import socket
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from .router import _SKIP_DIRS

#: Exact variable names always removed from an execution-plane environment.
PROVIDER_SECRET_NAMES = frozenset(
    {
        "OPENAI_API_KEY", "OPENAI_ORG_ID", "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT", "GH_ENTERPRISE_TOKEN",
        "NPM_TOKEN", "NODE_AUTH_TOKEN", "PYPI_TOKEN", "TWINE_PASSWORD",
        "CARGO_REGISTRY_TOKEN", "DOCKER_PASSWORD", "HF_TOKEN",
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "MISTRAL_API_KEY",
        "GROQ_API_KEY", "DEEPSEEK_API_KEY", "NVIDIA_API_KEY",
        "OPENROUTER_API_KEY", "PERPLEXITY_API_KEY", "COHERE_API_KEY",
    }
)

#: Prefixes whose whole family is removed (cloud provider credential sets).
PROVIDER_SECRET_PREFIXES = ("AWS_", "AZURE_", "GCP_", "GOOGLE_CLOUD_", "CLOUDSDK_")

#: Any variable whose name contains one of these as a whole component is a
#: credential by convention. Deliberately broad: a false positive costs a
#: scanner an environment variable, a false negative leaks a live credential.
_SECRETISH = re.compile(
    r"(?i)(?:^|_)(?:api[_-]?keys?|apikey|secrets?|tokens?|passwd|password|"
    r"credentials?|private[_-]?key|access[_-]?key|auth)(?:_|$)"
)

#: Kept even though the name looks secret-ish: dropping them breaks the child
#: process rather than protecting anything.
_ENV_KEEP = frozenset({"PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL"})


class GuardError(Exception):
    """Raised when a guarantee this module makes was violated."""


# --------------------------------------------------------------------------- #
# 1. Secret denylist
# --------------------------------------------------------------------------- #
def is_secret_name(name: str) -> bool:
    upper = name.upper()
    if upper in _ENV_KEEP:
        return False
    if upper in PROVIDER_SECRET_NAMES:
        return True
    if any(upper.startswith(p) for p in PROVIDER_SECRET_PREFIXES):
        return True
    return bool(_SECRETISH.search(upper))


def scrub_env(base: Optional[Dict[str, str]] = None) -> Tuple[Dict[str, str], List[str]]:
    """Environment with every credential removed, plus the names dropped.

    Returns ``(env, removed)`` so the run can record *what* was withheld: a
    guard that cannot say what it did is indistinguishable from one that did
    nothing.
    """
    source = dict(os.environ if base is None else base)
    removed = sorted(name for name in source if is_secret_name(name))
    for name in removed:
        source.pop(name, None)
    return source, removed


# --------------------------------------------------------------------------- #
# 2. Read-only target
# --------------------------------------------------------------------------- #
class Fingerprint(NamedTuple):
    digest: str
    file_count: int


def fingerprint(target: Path) -> Fingerprint:
    """Content hash of the target tree (skipping build/vcs noise).

    Hashes file *contents*, not mtimes: a tool that rewrites a file with
    identical bytes has not changed the target, and flagging it would train the
    user to ignore the check.
    """
    target = Path(target)
    hasher = hashlib.sha256()
    count = 0
    entries: List[Tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in sorted(filenames):
            path = Path(dirpath) / name
            try:
                entries.append((path.relative_to(target).as_posix(), path))
            except ValueError:  # pragma: no cover - defensive
                continue
    for rel, path in sorted(entries):
        try:
            data = path.read_bytes()
        except OSError:
            # An unreadable file still counts, by name: silently skipping it
            # would let a tool hide a mutation behind a permission error.
            data = b"<unreadable>"
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(hashlib.sha256(data).digest())
        count += 1
    return Fingerprint(hasher.hexdigest(), count)


class ReadOnlyTarget:
    """Assert that a run left the target byte-identical.

    ::

        with ReadOnlyTarget(repo):
            run_phases()      # raises GuardError if anything changed
    """

    def __init__(self, target: Path):
        self.target = Path(target)
        self.before: Optional[Fingerprint] = None
        self.after: Optional[Fingerprint] = None

    def __enter__(self) -> "ReadOnlyTarget":
        self.before = fingerprint(self.target)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.after = fingerprint(self.target)
        if exc_type is None and self.before != self.after:
            raise GuardError(
                "target was modified during the run (%s -> %s, %d -> %d files): "
                "the audited tree must stay read-only"
                % (self.before.digest[:12], self.after.digest[:12],
                   self.before.file_count, self.after.file_count)
            )
        return False


def ephemeral_copy(target: Path, parent: Optional[Path] = None) -> Path:
    """Throwaway copy of the target, for the modes that must run its code.

    The caller owns the returned directory and is responsible for deleting it;
    :class:`EphemeralTarget` does that automatically.
    """
    destination = Path(tempfile.mkdtemp(prefix="phases-target-", dir=str(parent) if parent else None))
    shutil.copytree(
        Path(target),
        destination / Path(target).name,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*sorted(_SKIP_DIRS)),
    )
    return destination / Path(target).name


class EphemeralTarget:
    """Context manager around :func:`ephemeral_copy`."""

    def __init__(self, target: Path, parent: Optional[Path] = None):
        self.source = Path(target)
        self._parent = parent
        self.path: Optional[Path] = None

    def __enter__(self) -> Path:
        self.path = ephemeral_copy(self.source, self._parent)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self.path is not None and self.path.parent.exists():
            shutil.rmtree(self.path.parent, ignore_errors=True)
        self.path = None
        return False


# --------------------------------------------------------------------------- #
# 3. Network policy (honest about its strength)
# --------------------------------------------------------------------------- #
ENFORCED = "enforced"
ADVISORY = "advisory"


def _closed_loopback_port() -> int:
    """A port nothing listens on, used as a black-hole proxy target."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return port  # closed again as soon as the socket is released


def network_policy() -> str:
    """``enforced`` only where a real per-process namespace is available.

    Nothing in this module creates a namespace, so the answer is ``advisory``
    everywhere today. The function exists so the run records the platform's
    actual capability instead of a constant, and so a future WSL/container
    backend has one place to flip.
    """
    return ADVISORY if sys.platform.startswith("win") else ADVISORY


def offline_env(base: Optional[Dict[str, str]] = None) -> Tuple[Dict[str, str], str]:
    """Environment steering HTTP clients into a closed port, plus the policy.

    Returns ``(env, policy)``. ``policy`` is :data:`ADVISORY` on every platform
    this ships on: a raw socket bypasses proxy variables entirely. Callers must
    surface that word in the report rather than translating it to "offline".
    """
    env = dict(os.environ if base is None else base)
    sink = "http://127.0.0.1:%d" % _closed_loopback_port()
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                 "http_proxy", "https_proxy", "all_proxy"):
        env[name] = sink
    env["NO_PROXY"] = ""
    env["no_proxy"] = ""
    # Common opt-outs for tools that phone home on start-up.
    env["DO_NOT_TRACK"] = "1"
    env["SEMGREP_SEND_METRICS"] = "off"
    env["PIP_NO_INDEX"] = "1"
    env["npm_config_offline"] = "true"
    return env, network_policy()


def execution_env(base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The full execution-plane environment: scrubbed, then steered offline."""
    scrubbed, _removed = scrub_env(base)
    env, _policy = offline_env(scrubbed)
    return env


def audit_env_report(base: Optional[Dict[str, str]] = None) -> Dict:
    """What the guards did, for the run report."""
    _env, removed = scrub_env(base)
    return {
        "secrets_removed": removed,
        "secrets_removed_count": len(removed),
        "network_policy": network_policy(),
        "network_note": (
            "proxy variables point at a closed loopback port; well-behaved HTTP "
            "clients fail, raw sockets are NOT blocked (no per-process network "
            "namespace on this platform)"
        ),
    }


def assert_no_secrets(env: Dict[str, str]) -> None:
    """Raise if any credential survived into ``env``."""
    leaked = sorted(name for name in env if is_secret_name(name))
    if leaked:
        raise GuardError("provider secrets leaked into the execution plane: %s" % ", ".join(leaked))
