"""Cross-platform data directory for the bundled Multidim MCP server.

``phases-oss`` is a **zero-dependency** package, so this deliberately does not
use ``platformdirs``: it resolves a dedicated, portable data directory with the
standard library alone, following the same platform conventions.

Resolution order:

1. ``$PHASES_OSS_HOME`` if set (explicit override, expanded);
2. Windows  -> ``%LOCALAPPDATA%\\phases-oss`` (then ``%APPDATA%``);
3. macOS    -> ``~/Library/Application Support/phases-oss``;
4. other    -> ``$XDG_DATA_HOME/phases-oss`` or ``~/.local/share/phases-oss``.

The Multidim store lives at ``<data_dir>/multidim/store.json``.

This module NEVER points at ``~/.multidim`` -- that path belongs to a separate,
personal installation and must never be read, written, migrated or overwritten
by the bundled OSS server.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "phases-oss"
_ENV_OVERRIDE = "PHASES_OSS_HOME"

# A hard tripwire: the OSS server must never resolve to the personal store.
_FORBIDDEN_PERSONAL = os.path.join("~", ".multidim")


def _home() -> Path:
    # ``Path.home()`` can raise on an environment with no HOME; fall back to cwd
    # rather than crash, so the server still starts (in a local ``.phases-oss``).
    try:
        return Path.home()
    except (RuntimeError, OSError):
        return Path(os.getcwd())


def data_dir() -> Path:
    """Return the dedicated, portable data directory (not created here)."""
    override = os.environ.get(_ENV_OVERRIDE)
    if override and override.strip():
        return Path(os.path.expanduser(override.strip()))

    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base and base.strip():
            return Path(base) / APP_NAME
        return _home() / "AppData" / "Local" / APP_NAME

    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support" / APP_NAME

    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg and xdg.strip():
        return Path(xdg) / APP_NAME
    return _home() / ".local" / "share" / APP_NAME


def multidim_dir() -> Path:
    return data_dir() / "multidim"


def assert_not_personal(path) -> None:
    """Raise if ``path`` is the personal ``~/.multidim`` dir or anywhere beneath.

    Called before every store read AND write, so even an explicit path argument
    (bypassing :func:`store_path`) can never touch the personal store.
    ``relative_to`` succeeds (returns ``.`` or a subpath) exactly when ``path`` is
    equal to or under ``personal``; ``resolve()`` also defeats a symlink pointing
    into the personal store.
    """
    personal = Path(os.path.expanduser(_FORBIDDEN_PERSONAL))
    try:
        personal = personal.resolve()
    except OSError:
        pass
    try:
        resolved = Path(path).resolve()
    except OSError:
        resolved = Path(path)
    try:
        resolved.relative_to(personal)
        inside_personal = True
    except ValueError:
        inside_personal = False
    if inside_personal:
        raise RuntimeError(
            "refus: le store OSS ne doit jamais pointer dans ~/.multidim (store personnel)"
        )


def store_path() -> Path:
    """Absolute path of the OSS Multidim store (dedicated, never ~/.multidim)."""
    path = multidim_dir() / "store.json"
    assert_not_personal(path)  # defence in depth on the default path too
    return path
