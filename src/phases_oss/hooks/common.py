"""Shared helpers for the phase hooks.

A *hook* reads a JSON payload from the agent harness on stdin, consults the
active phase, and may print a decision object on stdout. Everything that can be
unit-tested lives in pure functions here and in the per-hook ``decide()``; the
``main()`` wrappers only do stdin/stdout.

Honest framing: these hooks enforce *discipline*, not security. They walk up
from the current directory to find ``.claude/phase-state.json``; an agent with
write access to that file can lift any restriction. The hooks fail OPEN (allow)
on any doubt, so they never wedge an unrelated session.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional, Tuple

STATE_REL = Path(".claude") / "phase-state.json"
MARKER_REL = Path(".claude") / "objective-lock.off"
GUARDED_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def find_upwards(start: Path, rel: Path) -> Optional[Path]:
    """Return ``<dir>/rel`` for the nearest ancestor of ``start`` that has it."""
    start = start.resolve()
    for d in (start, *start.parents):
        cand = d / rel
        if cand.exists():
            return cand
    return None


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def sanitize(value, maxlen: int = 160) -> str:
    """Strip control characters and collapse whitespace before echoing.

    Used on anything taken from the (untrusted) phase state so a crafted
    objective cannot inject control sequences into a hook message.
    """
    if not value:
        return ""
    # Strip C0/C1 controls AND the Unicode line/paragraph separators
    # (U+0085, U+2028, U+2029) that would otherwise survive as line breaks.
    text = re.sub(r"[\x00-\x1f\x7f  ]", " ", str(value))
    text = re.sub(r"\s{2,}", " ", text).strip()
    return (text[:maxlen] + "…") if len(text) > maxlen else text


def project_dir_of(state_path: Path) -> Path:
    # <project>/.claude/phase-state.json -> <project>
    return state_path.parent.parent


def is_home_locked(project_dir: Path) -> bool:
    """A state at the user's home (or ~/.claude) must not enforce globally.

    This is the anti self-lock guard: a forgotten phase-state in the home
    directory would otherwise block every edit in every session.
    """
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        return False
    pd = project_dir.resolve()
    return pd == home or pd == (home / ".claude")


def normalize(path_str, base: Path) -> Optional[str]:
    """Absolute, case-folded (per-OS), separator-trimmed path for comparison."""
    if not path_str:
        return None
    try:
        p = Path(path_str)
        if not p.is_absolute():
            p = base / p
        # os.path.normcase folds case on Windows (case-insensitive FS) but
        # preserves it on POSIX -- so Foo.py and foo.py are distinct on Linux
        # and the same on Windows, matching the real filesystem.
        return os.path.normcase(str(p.resolve())).rstrip("\\/")
    except (OSError, ValueError):
        return None


def active_phase(start: Path) -> Tuple[Optional[Path], Optional[dict]]:
    """Find an *active, approved, non-home-locked* phase governing ``start``.

    Returns ``(state_path, phase)`` or ``(None, None)`` (fail open).
    """
    if find_upwards(start, MARKER_REL):
        return None, None
    state_path = find_upwards(start, STATE_REL)
    if state_path is None:
        return None, None
    phase = load_json(state_path)
    if not phase or not phase.get("active") or phase.get("status") != "active":
        return None, None
    if is_home_locked(project_dir_of(state_path)):
        return None, None
    return state_path, phase
