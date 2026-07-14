"""PreToolUse hook: keep edits inside the active phase's files_allowed.

Fires before Edit/Write/MultiEdit/NotebookEdit and before Bash. If a phase is
active, an edit to a file outside ``files_allowed`` is denied (re-gate), and a
Bash command that writes a file is denied (so a shell write cannot slip past the
edit checkpoint). Git's own writes (to ``.git``) are allowed.

Fails OPEN on anything unexpected.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from typing import Optional

from .common import (
    GUARDED_TOOLS,
    active_phase,
    normalize,
    project_dir_of,
    sanitize,
)

# Devices that are sinks, not real files: a redirect to one writes nothing.
_STD_SINKS = {"/dev/null", "/dev/stdout", "/dev/stderr"}
# Commands that write a file by themselves (bare command name at a segment head,
# or anywhere in a non-exempt segment). ``sed`` is handled separately: it only
# writes with ``-i``/``--in-place``.
_WRITE_TOOLS = {
    "tee", "cp", "mv", "dd", "install", "truncate", "ln", "patch",
    "Set-Content", "Add-Content", "Out-File",
}
# A segment whose command name is one of these is exempt from the write-TOOL
# check: ``git`` (its own writes go to .git; ``git mv`` is a subcommand, not the
# ``mv`` tool), ``cd``, and ``echo``. Redirects are still checked for them.
_WRITE_EXEMPT = {"git", "cd", "echo"}
# Control operators that separate one command from the next. ``|&`` is bash's
# pipe-stdout-and-stderr operator (shlex groups it as one punctuation token).
_CONTROL_OPS = {"&&", "||", ";", "|", "|&", "&"}
# A backslash-escaped shell metacharacter is a literal, not an operator. We
# neutralise it BEFORE tokenising (shlex would otherwise collapse `\&` to a bare
# `&` indistinguishable from the operator). Double backslashes are protected
# first so `\\&` keeps its real operator `&`.
_NEUTRAL = "\x00"
_BACKSLASH_PAIR = "\x01"
_ESCAPED_META = re.compile(r"\\([;&|<>()])")


def bash_writes_file(command: str) -> bool:
    """True if a Bash command writes a project file (git-only writes excepted).

    Tokenises with ``shlex`` (POSIX + ``punctuation_chars``) so quotes and
    escapes are honoured: a separator or a write-tool name that sits inside
    quotes is not mistaken for a real operator or command. A malformed command
    (unbalanced quotes) is treated as a write, i.e. the safe direction.

    Deliberately NOT covered (documented so callers do not mistake this for a
    security boundary -- it is a discipline checkpoint, the CI diff is the real
    backstop):
    - writes through an interpreter, e.g. ``python -c "open(f,'w')..."`` or
      ``node -e``, which carry no redirect or write-tool token;
    - command substitution ``$(...)`` / backticks, and here-documents;
    - a newline that occurs *inside* a quoted multi-line string (it is treated
      as a command separator);
    - a lone operator character sitting *alone* inside quotes, e.g.
      ``echo ">" harmless`` -- shlex strips the quotes and the token value ``>``
      is then indistinguishable from a real operator, so it over-DENIES (the
      safe direction for a checkpoint);
    - a file descriptor bound to a file and then written indirectly, e.g.
      ``exec 3<>f; printf x >&3`` (tracking fds across commands needs a real
      shell, not a tokeniser).
    Fully closing these last two classes requires a real shell parser (bashlex);
    deliberately out of scope to keep this a dependency-free discipline check.
    """
    # Force-clobber `>|` behaves as `>`; a newline is a command separator that
    # shlex would otherwise swallow as whitespace, so make it explicit.
    command = command.replace(">|", ">").replace("\n", " ; ")
    # Protect `\\` (a literal backslash) before neutralising single `\<meta>`.
    work = command.replace("\\\\", _BACKSLASH_PAIR)
    work = _ESCAPED_META.sub(_NEUTRAL, work)
    work = work.replace(_BACKSLASH_PAIR, "\\\\")
    try:
        lexer = shlex.shlex(work, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""  # `#` starts no comment: `> #file.py` is a real write.
        tokens = list(lexer)
    except ValueError:
        return True  # malformed (e.g. unbalanced quotes): be conservative.

    # A redirection to a real file (anywhere). `>`/`>>` write/append stdout and
    # `<>` opens a file read-write; the both-streams forms `>&`/`&>`/`&>>` write a
    # file UNLESS the target is a bare fd number (`2>&1`, `>&2`) -- those
    # duplicate a descriptor, no file. `>|` was already normalised to `>`. A
    # `/dev/null|stdout|stderr` target is a sink.
    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok in (">", ">>", "<>") and nxt and nxt not in _STD_SINKS:
            return True
        if tok in (">&", "&>", "&>>") and nxt and not nxt.isdigit() and nxt not in _STD_SINKS:
            return True

    # A write-tool command in any non-exempt segment.
    def _segment_writes(seg: list) -> bool:
        if not seg:
            return False
        # `sed` writes in place with -i / -i<suffix> / --in-place[=suffix], even
        # behind a wrapper (`sudo sed -i`, `xargs sed -i`), so check anywhere.
        if "sed" in seg and any(
            a == "-i" or a.startswith("-i") or a.startswith("--in-place")
            for a in seg
        ):
            return True
        if seg[0] in _WRITE_EXEMPT:
            return False
        return any(word in _WRITE_TOOLS for word in seg)

    segment: list = []
    for tok in tokens:
        if tok in _CONTROL_OPS:
            if _segment_writes(segment):
                return True
            segment = []
        else:
            segment.append(tok)
    return _segment_writes(segment)


def _deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def decide(payload: dict, start: Path) -> Optional[dict]:
    state_path, phase = active_phase(start)
    if phase is None:
        return None

    tool = str(payload.get("tool_name") or "")
    base = project_dir_of(state_path)

    if tool == "Bash":
        command = str((payload.get("tool_input") or {}).get("command") or "")
        if bash_writes_file(command):
            return _deny(
                "objective-lock: writing files via Bash is blocked during an "
                "active phase (objective: %s). Use Write/Edit on a files_allowed "
                "entry, or reset the phase." % sanitize(phase.get("objective"))
            )
        return None

    if tool not in GUARDED_TOOLS:
        return None

    tool_input = payload.get("tool_input") or {}
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not target:
        return None

    target_norm = normalize(target, base)
    if target_norm is None:
        return None

    # The phase-state file itself is always writable.
    if target_norm == normalize(str(state_path), base):
        return None

    for allowed in phase.get("files_allowed", []):
        if normalize(allowed, base) == target_norm:
            return None

    return _deny(
        "objective-lock: '%s' is outside files_allowed for the active phase "
        "(objective: %s). Re-gate: widen files_allowed via a new init, or edit "
        "an allowed file." % (sanitize(str(target), 80), sanitize(phase.get("objective")))
    )


def main(argv=None) -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return 0
    decision = decide(payload, Path.cwd())
    if decision is not None:
        sys.stdout.write(json.dumps(decision))
    return 0


if __name__ == "__main__":
    sys.exit(main())
