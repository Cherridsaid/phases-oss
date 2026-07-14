"""Install the phase hooks into a target project's ``.claude/settings.json``.

Safety contract:

* **Project-local only.** The installer writes exactly one file:
  ``<project>/.claude/settings.json``. It refuses to target the user's home
  directory or ``~/.claude`` (the *global* config), so a global install is
  impossible.
* **Dry-run by default.** Without ``--apply`` it only prints the plan; nothing
  is written.
* **Non-destructive merge.** Existing hooks and settings are preserved; the
  phase hooks are appended only if not already present (idempotent).

It is intended to be created and reviewed, then run by the *user* against their
own project -- it is never executed against a real project during this package's
own build (its tests operate on throwaway temp directories).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# matcher -> hook module. Bash is included so the PreToolUse hook can guard
# shell writes, not just Edit/Write.
PRE_TOOL_MATCHER = "Edit|Write|MultiEdit|NotebookEdit|Bash"
HOOK_MODULES = {
    "PreToolUse": ("phases_oss.hooks.pre_tool_use", PRE_TOOL_MATCHER),
    "Stop": ("phases_oss.hooks.stop", None),
    "UserPromptSubmit": ("phases_oss.hooks.user_prompt_submit", None),
}


class InstallError(Exception):
    """Raised when the target is refused (e.g. the global config)."""


def _command(module: str, python_exe: str) -> str:
    return "%s -m %s" % (python_exe, module)


def hook_entry(module: str, python_exe: str, matcher: Optional[str]) -> dict:
    entry: Dict[str, object] = {"hooks": [{"type": "command", "command": _command(module, python_exe)}]}
    if matcher is not None:
        entry["matcher"] = matcher
    return entry


def _is_global_target(project: Path) -> bool:
    """True if ``project`` is the user's home or ``~/.claude`` (the global config)."""
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        return False
    p = project.resolve()
    return p == home or p == (home / ".claude")


def _command_present(event_list: List[dict], command: str) -> bool:
    for group in event_list:
        for hook in group.get("hooks", []):
            if hook.get("command") == command:
                return True
    return False


def plan(project: Path, python_exe: str = "python") -> Tuple[dict, List[str]]:
    """Return the merged settings and a human-readable list of changes.

    Does not write anything. Raises :class:`InstallError` for a global target.
    """
    project = Path(project)
    if _is_global_target(project):
        raise InstallError(
            "refusing to install into the global config (%s). Target a specific "
            "project directory instead." % project
        )

    claude_dir = project / ".claude"
    settings_path = claude_dir / "settings.json"

    # Re-check the ACTUAL write location, following any symlink, so a planted
    # `<project>/.claude -> ~/.claude` symlink cannot smuggle a global write
    # past the project-level check above.
    if claude_dir.exists():
        resolved_dir = claude_dir.resolve()
        try:
            home = Path.home().resolve()
        except (OSError, RuntimeError):
            home = None
        if home is not None and resolved_dir in (home, home / ".claude"):
            raise InstallError(
                "refusing: %s resolves to the global config (%s)" % (claude_dir, resolved_dir)
            )

    # Read existing settings. Refuse (never overwrite) anything that is not a
    # well-formed JSON object -- destroying a user's file is worse than failing.
    settings: Dict[str, object] = {}
    if settings_path.exists():
        try:
            loaded = json.loads(settings_path.read_text(encoding="utf-8"))
        except ValueError:
            raise InstallError(
                "existing %s is not valid JSON; fix or remove it before installing" % settings_path
            )
        if not isinstance(loaded, dict):
            raise InstallError(
                "existing %s root is not a JSON object; refusing to overwrite" % settings_path
            )
        settings = loaded

    hooks = settings.get("hooks")
    if hooks is None:
        hooks = {}
    elif not isinstance(hooks, dict):
        raise InstallError("existing 'hooks' is not an object; refusing to overwrite")
    # work on a shallow copy so plan() stays pure
    hooks = {k: list(v) if isinstance(v, list) else v for k, v in hooks.items()}

    changes: List[str] = []
    for event, (module, matcher) in HOOK_MODULES.items():
        command = _command(module, python_exe)
        event_list = hooks.get(event)
        if event_list is None:
            event_list = []
        elif not isinstance(event_list, list):
            raise InstallError("existing hooks[%r] is not a list; refusing to overwrite" % event)
        if _command_present(event_list, command):
            changes.append("= %s already configured (%s)" % (event, command))
        else:
            event_list = event_list + [hook_entry(module, python_exe, matcher)]
            changes.append("+ %s : %s" % (event, command))
        hooks[event] = event_list

    merged = dict(settings)
    merged["hooks"] = hooks
    return merged, changes


def apply(project: Path, python_exe: str = "python") -> Path:
    """Write the merged settings to ``<project>/.claude/settings.json``."""
    merged, _changes = plan(project, python_exe)
    settings_path = Path(project) / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return settings_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="phases-install",
        description="Install the phase hooks into a project's .claude/settings.json (dry-run by default).",
    )
    parser.add_argument("project", help="target project directory")
    parser.add_argument("--apply", action="store_true", help="actually write (default: dry-run)")
    parser.add_argument("--python", default="python", help="python executable for the hook command")
    args = parser.parse_args(argv)

    project = Path(args.project)
    try:
        merged, changes = plan(project, args.python)
    except InstallError as exc:
        print("install refused: %s" % exc, file=sys.stderr)
        return 2

    print("Plan for %s:" % (project / ".claude" / "settings.json"))
    for change in changes:
        print("  " + change)

    if not args.apply:
        print("\nDry-run (no file written). Re-run with --apply to write.")
        return 0

    path = apply(project, args.python)
    print("\nWrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
