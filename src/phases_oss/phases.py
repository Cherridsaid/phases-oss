"""Phase state machine for phases-oss.

A *phase* is a small, bounded unit of work. It moves through explicit gates:

    init -> approve -> (code) -> prove -> audit -> [review] -> close

Every phase carries a **risk level 0-3** that normalizes the whole policy:

    level 0  targeted proof, no review
    level 1  standard proof + one independent review
    level 2  full test suite required (``full_suite=True``) + strict review
    level 3  level 2 + runtime proof + explicit human validation

State lives in ``<repo>/.claude/phase-state.json``; every transition appends a
reconstructible v2 event to ``<repo>/.claude/phase-log.jsonl`` (see
``phases_oss.events``). Both are local-only artifacts; they are never
published (see the project ``.gitignore``).

Design notes
------------
* The exit code of the proof command is the single source of truth for
  "did it work". ``prove`` records it; ``close`` re-runs the proof against the
  *committed* tree (not the working tree) so a green working copy cannot be
  passed off as a green commit.
* The two side-effecting steps -- running the proof and verifying the commit --
  are injectable (``runner`` / ``verifier``) so the logic is unit-testable
  without spawning real subprocesses or git worktrees.
* This module is a discipline aid, not a security boundary. Nothing here can
  stop a determined process on the same machine; the real authority is
  deterministic tests, CI, and human review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

STATE_VERSION = 4
STATE_DIRNAME = ".claude"
STATE_FILENAME = "phase-state.json"
LOG_FILENAME = "phase-log.jsonl"

# Legacy audit names still accepted at init; each maps to a risk level 0-3.
AUDIT_TO_LEVEL: Dict[str, int] = {
    "none": 0,
    "review": 1,
    "security": 2,
    "critical": 3,
}
# Kept for backward compatibility with callers that import it; under the
# normalized policy the number of required reviews is 0 at level 0 and 1 at
# levels 1-3 (one independent review, stricter at higher levels).
AUDIT_THRESHOLDS = AUDIT_TO_LEVEL

# Per-level policy tables (index = risk level).
LEVEL_PROOF_SCOPE = ("targeted", "standard", "full", "full")
LEVEL_REVIEW_MODE = ("none", "standard", "strict", "strict")
# Expected pre-phase analysis depth per level (when the analysis gate is on).
LEVEL_ANALYSIS_DEPTH = ("core", "core", "deep", "full")

# Pre-phase analysis artifact reference: ``artifact://<store>/<id>``. The
# journal keeps this reference and the analysis *metadata* only -- the full
# analysis text stays in the artifact store, never copied into the log.
_ANALYSIS_REF = re.compile(r"^artifact://[a-z0-9][a-z0-9_-]*/[A-Za-z0-9._-]+$")


def reviews_required(level: int) -> int:
    """Number of independent review reports required to close at ``level``."""
    return 0 if level <= 0 else 1


def _phase_level(data: Dict) -> int:
    """Risk level of a stored phase, fail-closed for legacy states.

    A pre-v4 state has no ``risk_level``; falling back to 0 would silently
    drop its audit requirement at the very gate that matters. Derive the
    level from the legacy ``audit_required`` name instead, and treat an
    unknown name as the strictest level, never the weakest.
    """
    value = data.get("risk_level")
    if value is not None:
        return int(value)
    return AUDIT_TO_LEVEL.get(str(data.get("audit_required", "none")), 3)

# A proof that asserts nothing is not a proof.
_TRIVIAL_PROOF = re.compile(r"^\s*(exit\s+0|true|:)\s*$", re.IGNORECASE)

_MIN_REPORT_BYTES = 200
_PROOF_OUTPUT_TAIL = 4000

# Type aliases for the injectable side effects.
Runner = Callable[[str, Path], Tuple[int, str]]
Verifier = Callable[[Path, str, str], bool]
Reviewer = Callable[["Phase"], "ReviewVerdict"]
# A repo guard raises PhaseError when the commit does not belong to the repo
# that actually holds the files_allowed. Injectable so unit tests need no git.
RepoGuard = Callable[[Path, Sequence[str], str], None]


class PhaseError(Exception):
    """Raised when a phase transition is invalid or a gate is not satisfied."""


class ReviewVerdict:
    """Result of a reviewer pass over a phase.

    Four verdicts exist: ``PASS``, ``PASS_WITH_NOTES`` (pass, with findings
    worth reading), ``REFUS`` and ``UNAVAILABLE``. ``UNAVAILABLE`` is the
    fail-closed verdict: the reviewer backend was absent, unreachable, timed
    out, or replied with something unparseable. It is **never** treated as a
    pass -- a review that did not happen cannot approve anything. REFUS and
    UNAVAILABLE both block close.
    """

    PASS = "PASS"
    PASS_WITH_NOTES = "PASS_WITH_NOTES"
    REFUS = "REFUS"
    UNAVAILABLE = "REVIEW_UNAVAILABLE"

    def __init__(self, verdict: str, notes: str = "", action: str = ""):
        if verdict not in (self.PASS, self.PASS_WITH_NOTES, self.REFUS, self.UNAVAILABLE):
            raise ValueError("unknown verdict: %r" % verdict)
        self.verdict = verdict
        self.notes = notes
        self.action = action

    @property
    def passed(self) -> bool:
        return self.verdict in (self.PASS, self.PASS_WITH_NOTES)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def state_paths(root: Path) -> Dict[str, Path]:
    state_dir = root / STATE_DIRNAME
    return {
        "dir": state_dir,
        "state": state_dir / STATE_FILENAME,
        "log": state_dir / LOG_FILENAME,
    }


class Phase:
    """Thin object wrapper around the persisted phase dict."""

    def __init__(self, data: Dict):
        self.data = data

    # Convenience accessors used by callers / reviewers.
    @property
    def objective(self) -> str:
        return self.data.get("objective", "")

    @property
    def files_allowed(self) -> List[str]:
        return list(self.data.get("files_allowed", []))

    @property
    def audit_required(self) -> str:
        return self.data.get("audit_required", "none")

    def __getitem__(self, key):
        return self.data[key]

    def get(self, key, default=None):
        return self.data.get(key, default)


def load_state(root: Path) -> Optional[Phase]:
    path = state_paths(root)["state"]
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        # A corrupted state must surface as an actionable PhaseError on every
        # command, not as a raw traceback. reset is the escape hatch (it
        # journals a terminal snapshot when it still can).
        raise PhaseError(
            "phase state file is corrupted (%s): %s -- fix it by hand or "
            "abandon the phase with reset" % (exc.__class__.__name__, path)
        )
    if not isinstance(data, dict):
        raise PhaseError(
            "phase state file does not hold an object: %s -- fix it by hand "
            "or abandon the phase with reset" % path
        )
    return Phase(data)


def save_state(root: Path, phase: Phase) -> None:
    """Persist the state atomically: full temp write, fsync, then rename.

    A crash mid-write must never leave a half-truncated state file behind --
    os.replace is atomic on POSIX and on Windows (same volume).
    """
    paths = state_paths(root)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    target = paths["state"]
    tmp = target.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(phase.data, indent=2, ensure_ascii=False))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)


def _emit(root: Path, event_type: str, phase: Phase, **kwargs) -> None:
    """Persist the state (ids included) then append a v2 journal event."""
    from phases_oss import events as _events

    state_dir = state_paths(root)["dir"]
    _events.ensure_ids(phase.data, state_dir)
    save_state(root, phase)
    _events.write_event(state_dir, event_type, phase.data, **kwargs)


def _require_active(root: Path, *, status: Optional[str] = None) -> Phase:
    phase = load_state(root)
    if phase is None or not phase.data.get("active"):
        raise PhaseError("no active phase")
    if status is not None and phase.data.get("status") != status:
        raise PhaseError(
            "phase status is %r, expected %r" % (phase.data.get("status"), status)
        )
    return phase


# --------------------------------------------------------------------------- #
# Default side effects (real subprocess / git). Injectable for tests.
# --------------------------------------------------------------------------- #
def default_runner(
    command: str, cwd: Path, env: Optional[Dict[str, str]] = None
) -> Tuple[int, str]:
    """Run ``command`` in a shell and return (exit_code, merged_output).

    stderr is merged into stdout so warnings stay visible, but the exit code
    remains the authority. Output is never silenced. ``env`` defaults to None,
    which inherits the parent environment; callers that need isolation (e.g. the
    verifier re-running a proof against a committed tree) pass an explicit map.

    Output is decoded as UTF-8 with ``errors="replace"``, never via the locale:
    on Windows the locale codec (cp1252) raises UnicodeDecodeError on common
    UTF-8 test-runner output (e.g. the bytes of ❌), which would crash ``prove``
    and lose the proof result. A replacement character in the log is acceptable;
    an unrecorded proof is not. The exit code is unaffected either way.
    """
    proc = subprocess.run(
        command,
        shell=True,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return proc.returncode, proc.stdout or ""


def _verify_env(worktree: Path) -> Dict[str, str]:
    """Environment for re-running a proof against the committed worktree.

    Strips the interpreter path knobs that would let the *working tree* satisfy
    the proof instead of the checked-out commit, and prepends the worktree's own
    source so imports resolve the commit first, ahead of any editable install.
    """
    env = dict(os.environ)
    for var in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME"):
        env.pop(var, None)
    env["PYTHONNOUSERSITE"] = "1"
    src = worktree / "src"
    env["PYTHONPATH"] = str(src if src.is_dir() else worktree)
    return env


def default_verifier(root: Path, commit_sha: str, proof_command: str) -> bool:
    """Re-run the proof against the committed tree in a throwaway worktree.

    Returns True only if the proof passes on the commit itself. If git is
    unavailable the verification cannot run and we conservatively return False
    so close does not silently accept an unverified commit.

    The proof runs with a cleaned environment (see ``_verify_env``): interpreter
    path knobs are dropped and the worktree source is prepended, so for a module
    that exists in the commit the import resolves the commit, not the live tree.
    This reduces working-tree leakage; it is NOT a hermetic sandbox.

    Honest residuals (NOT closed by this env scrub, by design):
    - A module present ONLY in the live working tree (absent from the commit)
      can still be imported through a leftover editable ``.pth`` in the active
      venv's site-packages, since we prepend but do not scrub site-packages.
    - A PEP 660 editable using an import-hook finder wins over ``PYTHONPATH``
      regardless of sys.path order.
    - A proof command hard-coding an absolute path outside the worktree escapes.
    Closing these would require a throwaway venv per verify; deliberately out of
    scope for a workflow-discipline tool (not a security boundary).
    """
    with tempfile.TemporaryDirectory(prefix="phases-verify-") as tmp:
        worktree = Path(tmp) / "wt"
        add = subprocess.run(
            ["git", "-C", str(root), "worktree", "add", "--detach", str(worktree), commit_sha],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
        )
        if add.returncode != 0:
            return False
        try:
            code, _ = default_runner(proof_command, worktree, env=_verify_env(worktree))
            return code == 0
        finally:
            subprocess.run(
                ["git", "-C", str(root), "worktree", "remove", "--force", str(worktree)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def _git_out(args: Sequence[str]) -> Optional[str]:
    """Run a read-only git command; return trimmed stdout or None on any error.

    Never raises: a git failure is turned into None so the *caller* decides the
    fail-closed policy explicitly (rather than a stray exception leaking).
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, ValueError):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip()


def _pathkey(value: str) -> str:
    """Comparison key for a filesystem path, honouring OS case rules.

    ``os.path.normcase`` lower-cases on Windows (case-insensitive FS) but is a
    no-op on Linux/macOS (case-sensitive), so ``A.py`` and ``a.py`` stay
    distinct where the filesystem keeps them distinct. Separators are unified to
    ``/`` afterwards so Windows ``\\`` and POSIX ``/`` compare equal.
    """
    return os.path.normcase(str(value)).replace("\\", "/")


def _git_ok(args: Sequence[str]) -> bool:
    """Run a git command for its exit code only (e.g. ``cat-file -e``).

    Returns True only on a clean exit 0. Any error (including git missing) is
    False, so a caller can fail closed.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        return False
    return proc.returncode == 0


def _repo_identity(directory: Path) -> Optional[str]:
    """Identity of the git repo containing ``directory``.

    Uses ``--git-common-dir`` (shared across worktrees) rather than the
    working-tree top level, so a throwaway verify worktree is still recognised
    as the same repo. Returns an absolute, normalised key, or None if the path
    is in no git repo.
    """
    common = _git_out(["-C", str(directory), "rev-parse", "--git-common-dir"])
    if not common:
        return None
    resolved = Path(common)
    if not resolved.is_absolute():
        resolved = (directory / resolved).resolve()
    try:
        return _pathkey(str(resolved.resolve())).rstrip("/")
    except OSError:
        return None


def default_repo_guard(root: Path, files_allowed: Sequence[str], commit_sha: str) -> None:
    """P28b repo-match guard (generic, no repo hardcoded).

    Refuses to close a phase when ``commit_sha`` does not belong to the git repo
    that actually holds the ``files_allowed`` -- the flaw that let a phase close
    against an unrelated project's commit. Raises ``PhaseError`` on any mismatch.

    Fail-closed: every git failure raises rather than silently allowing. The
    caller only invokes this when a commit_sha is present.
    """
    files = [f.strip() for f in files_allowed if f and f.strip()]
    if not files:
        raise PhaseError("repo-match: files_allowed is empty; no repo to resolve")

    identities: Dict[str, str] = {}
    entries: List[Tuple[Path, str, bool]] = []  # (directory, name, is_root_scope)
    root_abs = root.absolute()
    for f in files:
        p = Path(f)
        # ``.absolute()`` (not ``.resolve()``) so a *versioned symlink* keeps its
        # literal name -- git tracks the link, not its target.
        literal = (p if p.is_absolute() else (root / p)).absolute()
        # ``Path('R/.')`` normalises to ``Path('R')``, so test the resolved-to-
        # root equality, not ``literal.name`` (which would be 'R', not '.').
        if literal == root_abs or str(p) in (".", ""):
            directory, name, is_root = literal, "", True
        else:
            # Always the *parent*; whether ``name`` is a file, a directory or a
            # versioned symlink is decided by git's data, never Path.is_dir().
            directory, name, is_root = literal.parent, literal.name, False
        if not directory.exists():
            raise PhaseError("repo-match: missing directory for %r" % f)
        ident = _repo_identity(directory)
        if ident is None:
            raise PhaseError(
                "repo-match: %r is in no git repo (version it first)" % f
            )
        identities[ident] = ident
        entries.append((directory, name, is_root))

    if len(identities) > 1:
        raise PhaseError(
            "repo-match: files_allowed span %d git repos; one phase = one repo"
            % len(identities)
        )
    # A real directory inside the repo, safe as the cwd for git commands
    # (the git-common-dir identity is used only for the uniqueness check above).
    repo = str(entries[0][0])

    # The commit must exist in the resolved repo (exit-code check: cat-file -e
    # prints nothing on success, so we cannot use _git_out here).
    if not _git_ok(["-C", repo, "cat-file", "-e", commit_sha + "^{commit}"]):
        raise PhaseError(
            "repo-match: commit %s absent from the files_allowed repo (%s)"
            % (commit_sha, repo)
        )

    # Path-name case sensitivity is the repo's own core.ignorecase (git's source
    # of truth), not a guess from os.path.normcase -- correct on Linux (sensitive),
    # Windows and case-insensitive macOS (insensitive) alike.
    ignorecase = _git_out(["-C", repo, "config", "--get", "core.ignorecase"]) == "true"

    def _gitkey(value: str) -> str:
        v = str(value).replace("\\", "/")
        return v.lower() if ignorecase else v

    # The commit must touch at least one file of the perimeter.
    touched_raw = _git_out(
        ["-C", repo, "show", "--name-only", "--pretty=format:", commit_sha]
    )
    touched = {
        _gitkey(line.strip())
        for line in (touched_raw or "").splitlines()
        if line.strip()
    }

    hit = False
    for directory, name, is_root in entries:
        if is_root:
            # Whole-repo perimeter: any touched file counts.
            if touched:
                hit = True
                break
            continue
        # ``--show-prefix`` gives ``directory`` relative to the repo top-level,
        # from git itself: robust to a symlinked root, never resolves the name.
        prefix = _git_out(["-C", str(directory), "rev-parse", "--show-prefix"])
        if prefix is None:
            raise PhaseError("repo-match: cannot locate %r inside the repo" % name)
        rel = _gitkey(prefix + name)
        # One rule, no filesystem is_dir: an exact hit (a file or a versioned
        # symlink blob), or -- only when git itself stores paths beneath ``rel``
        # (a real directory) -- a child hit. A symlink stores no children, so it
        # can never false-match a commit under its target.
        if rel in touched or any(t.startswith(rel.rstrip("/") + "/") for t in touched):
            hit = True
            break
    if not hit:
        raise PhaseError(
            "repo-match: commit %s touches no file of files_allowed" % commit_sha
        )


# --------------------------------------------------------------------------- #
# Transitions
# --------------------------------------------------------------------------- #
def init_phase(
    root: Path,
    *,
    objective: str,
    files_allowed: Sequence[str],
    proof_command: str,
    audit: Optional[str] = None,
    level: Optional[int] = None,
    full_suite: bool = False,
    inherited_from: Optional[str] = None,
    require_analysis: bool = False,
    analysis_context: str = "",
    analysis_depth: str = "",
    analysis_axes: Sequence[str] = (),
    analysis_ref: str = "",
    analysis_store_schema: int = 2,
) -> Phase:
    # One open phase at a time: a silent overwrite would leave the previous
    # phase dangling in the journal (no terminal event) and let a blocked
    # level-2 phase be "escaped" by re-initing at level 0. Abandoning a phase
    # is an explicit transition: reset.
    existing = load_state(root)
    if existing is not None and existing.data.get("active"):
        raise PhaseError(
            "a phase is already open (status %r, objective %r): close it, or "
            "abandon it explicitly with reset before init"
            % (existing.data.get("status"), existing.data.get("objective"))
        )

    if not objective or not objective.strip():
        raise PhaseError("objective is required")
    if not proof_command or not proof_command.strip():
        raise PhaseError("proof_command is required")
    if _TRIVIAL_PROOF.match(proof_command):
        raise PhaseError(
            "trivial proof_command rejected (exit 0 / true / :): the proof must "
            "verify the objective"
        )

    # Risk level 0-3 is the policy; the legacy audit names are accepted as
    # aliases (none=0, review=1, security=2, critical=3).
    if level is not None:
        if level not in (0, 1, 2, 3):
            raise PhaseError("level must be 0, 1, 2 or 3 (got %r)" % level)
        resolved_level = level
    else:
        if audit is None:
            resolved_level = 0
        elif audit in AUDIT_TO_LEVEL:
            resolved_level = AUDIT_TO_LEVEL[audit]
        else:
            raise PhaseError("unknown audit level: %r" % audit)
    if resolved_level >= 2 and not full_suite:
        raise PhaseError(
            "level %d requires full_suite=True and a proof_command covering the "
            "project's whole test suite" % resolved_level
        )
    review_required = resolved_level >= 1

    # Pre-phase analysis gate (optional but strict once enabled): the phase
    # records analysis *metadata* -- the analysis itself lives in an artifact
    # store and is never copied into the state or the journal.
    analysis = None
    if require_analysis:
        expected_depth = LEVEL_ANALYSIS_DEPTH[resolved_level]
        axes = [a.strip() for chunk in analysis_axes for a in str(chunk).split(",") if a.strip()]
        if not analysis_context or not analysis_context.strip():
            raise PhaseError("ANALYSIS_CONTEXT_MISSING")
        if analysis_depth != expected_depth:
            raise PhaseError(
                "ANALYSIS_DEPTH_MISMATCH: level %d requires %r (got %r)"
                % (resolved_level, expected_depth, analysis_depth)
            )
        if not axes:
            raise PhaseError("ANALYSIS_AXES_MISSING")
        if not _ANALYSIS_REF.match(analysis_ref or ""):
            raise PhaseError(
                "ANALYSIS_REF_INVALID: expected artifact://<store>/<id> (got %r)"
                % analysis_ref
            )
        analysis = {
            "used": True,
            "context": analysis_context.strip(),
            "depth": analysis_depth,
            "axes": axes,
            "analysis_ref": analysis_ref,
            "store_schema": int(analysis_store_schema),
        }

    files = [f.strip() for f in files_allowed if f and f.strip()]
    if not files:
        raise PhaseError("files_allowed must list at least one file")

    phase = Phase(
        {
            "version": STATE_VERSION,
            "active": True,
            "status": "pending_approval",
            "objective": objective.strip(),
            "inherited_from": inherited_from or None,
            "files_allowed": files,
            "proof_command": proof_command,
            "proof_passed": False,
            "proof_exit_code": None,
            "proof_output": "",
            "audit_required": "review" if review_required else "none",
            "audit_passed": not review_required,
            "audit_agent_count": 0,
            "audit_reports": [],
            "review_verdict": None,
            "open_exploited": 0,
            "attempts": 0,
            "commit_sha": None,
            "lesson": None,
            "runtime_passed": False,
            "runtime_report": "",
            "human_validation_passed": False,
            "human_validated_at": None,
            "human_validator": None,
            "verify_passed": False,
            "risk_level": resolved_level,
            "proof_scope": LEVEL_PROOF_SCOPE[resolved_level],
            "full_suite_declared": bool(full_suite) if resolved_level >= 2 else False,
            "review_mode": LEVEL_REVIEW_MODE[resolved_level],
            "analysis_required": bool(require_analysis),
            "analysis": analysis,
            "created_at": _now(),
            "approved_at": None,
            "proved_at": None,
            "audited_at": None,
            "closed_at": None,
        }
    )
    _emit(root, "phase_initialized", phase)
    if require_analysis:
        # Metadata only, empty payload: the journal must never hold the
        # analysis text, only the pointer to it.
        _emit(root, "analysis.completed", phase, analysis=analysis, payload={})
    return phase


def approve(root: Path) -> Phase:
    phase = _require_active(root, status="pending_approval")
    phase.data["status"] = "active"
    phase.data["approved_at"] = _now()
    _emit(root, "phase_approved", phase)
    return phase


def prove(root: Path, *, runner: Optional[Runner] = None) -> int:
    phase = _require_active(root, status="active")
    runner = runner or default_runner
    code, output = runner(phase.data["proof_command"], root)
    phase.data["proof_exit_code"] = code
    phase.data["proof_passed"] = code == 0
    phase.data["proof_output"] = output[-_PROOF_OUTPUT_TAIL:]
    phase.data["proved_at"] = _now()
    if code == 0:
        phase.data["attempts"] = 0
    else:
        phase.data["attempts"] = int(phase.data.get("attempts", 0)) + 1
    # A new proof outdates every validation recorded against the previous
    # state of the tree: an audit, review verdict, runtime proof or human
    # validation taken before this run examined code that no longer exists.
    # Level 0 has no review requirement, so its audit gate stays satisfied.
    if reviews_required(_phase_level(phase.data)) > 0:
        phase.data["audit_passed"] = False
        phase.data["audit_agent_count"] = 0
        phase.data["audit_reports"] = []
        phase.data["audited_at"] = None
    phase.data["review_verdict"] = None
    phase.data["runtime_passed"] = False
    phase.data["runtime_report"] = ""
    phase.data["human_validation_passed"] = False
    phase.data["human_validated_at"] = None
    phase.data["human_validator"] = None
    phase.data["verify_passed"] = False
    _emit(root, "proof_completed", phase)
    return code


# The only verdicts a report may declare. Severity order matters: when a
# report carries several VERDICT lines, the strictest one wins.
_REPORT_VERDICTS = ("EXPLOITED", "REFUS", "PASS_WITH_NOTES", "PASS")
# The verdict token must be a whole word right after "VERDICT:"; free-text
# commentary may follow on the same line. \b keeps PASSABLE from reading as
# PASS -- the token captured there is "PASSABLE", which is not in the list.
_VERDICT_LINE = re.compile(r"(?im)^\s*VERDICT\s*:\s*([A-Za-z_]+)\b")


def _parse_report_verdict(text: str, source: str) -> str:
    """Return the strictest declared verdict, refusing anything unrecognized.

    A bare occurrence of the word VERDICT, or an invented value
    (``VERDICT: BANANA``, ``VERDICT: PASSABLE``), is not a verdict: the
    report is rejected rather than counted toward the audit.
    """
    found = [m.group(1).upper() for m in _VERDICT_LINE.finditer(text)]
    if not found:
        raise PhaseError(
            "report has no structured verdict line "
            "('VERDICT: PASS|PASS_WITH_NOTES|REFUS|EXPLOITED'): %s" % source
        )
    unknown = [v for v in found if v not in _REPORT_VERDICTS]
    if unknown:
        raise PhaseError(
            "report declares unrecognized verdict %r (allowed: %s): %s"
            % (unknown[0], ", ".join(_REPORT_VERDICTS), source)
        )
    for strictest in _REPORT_VERDICTS:
        if strictest in found:
            return strictest
    raise PhaseError("unreachable verdict state: %s" % source)  # pragma: no cover


def _validate_reports(reports: Sequence[str], need: int) -> Tuple[List[str], List[str]]:
    reports = [r.strip() for r in reports if r and r.strip()]
    if len(reports) < need:
        raise PhaseError(
            "audit requires %d distinct adversarial report(s); got %d"
            % (need, len(reports))
        )
    seen_hashes = set()
    resolved: List[str] = []
    verdicts: List[str] = []
    for r in reports:
        p = Path(r)
        if not p.exists():
            raise PhaseError("report not found: %s" % r)
        data = p.read_bytes()
        if len(data) < _MIN_REPORT_BYTES:
            raise PhaseError("report too short (<%d bytes): %s" % (_MIN_REPORT_BYTES, r))
        verdicts.append(_parse_report_verdict(data.decode("utf-8", "replace"), r))
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen_hashes:
            raise PhaseError("two identical reports (hash): audit not distinct")
        seen_hashes.add(digest)
        resolved.append(str(p.resolve()))
    return resolved, verdicts


def record_audit(
    root: Path,
    *,
    reports: Optional[Sequence[str]] = None,
    open_exploited: int = 0,
) -> Phase:
    # status="active" is load-bearing: without it, the reopen branch below
    # would flip a pending_approval phase straight to "active", silently
    # skipping the approve gate (audited bypass: init -> record_audit(EXPLOITED)
    # -> prove -> record_audit -> close, with approved_at=None).
    phase = _require_active(root, status="active")
    # Normalized policy: no review at level 0, one independent review at 1-3.
    need = reviews_required(_phase_level(phase.data))
    if need == 0:
        phase.data["audit_passed"] = True
        phase.data["audit_agent_count"] = 0
        phase.data["open_exploited"] = 0
    else:
        resolved, verdicts = _validate_reports(reports or [], need)
        phase.data["audit_agent_count"] = len(resolved)
        phase.data["audit_reports"] = resolved
        # The caller's open_exploited claim must not contradict the reports.
        if "EXPLOITED" in verdicts and open_exploited <= 0:
            raise PhaseError(
                "a report declares VERDICT: EXPLOITED but open_exploited=0: "
                "re-audit with the real count"
            )
        if "REFUS" in verdicts:
            phase.data["audit_passed"] = False
            phase.data["audited_at"] = _now()
            _emit(root, "audit_recorded", phase)
            raise PhaseError(
                "a report declares VERDICT: REFUS: fix, then re-audit"
            )
        if open_exploited > 0:
            phase.data["open_exploited"] = open_exploited
            phase.data["audit_passed"] = False
            phase.data["status"] = "active"
            phase.data["audited_at"] = _now()
            _emit(root, "audit_recorded", phase)
            raise PhaseError(
                "%d EXPLOITED finding(s): phase REOPENED (fix, then re-audit "
                "with open_exploited=0)" % open_exploited
            )
        phase.data["open_exploited"] = 0
        phase.data["audit_passed"] = True
    phase.data["audited_at"] = _now()
    _emit(root, "audit_recorded", phase)
    return phase


def review(root: Path, reviewer: Reviewer) -> ReviewVerdict:
    """Run a reviewer over the active phase and record its verdict.

    The reviewer is any callable taking the :class:`Phase` and returning a
    :class:`ReviewVerdict`. The static and cloud reviewers live in
    ``phases_oss.reviewers``; this function only orchestrates and records.
    A REFUS does not raise -- the caller decides whether to loop on the
    suggested action -- but it is persisted so ``close`` can be gated on it.
    """
    from phases_oss import events as _events

    phase = _require_active(root, status="active")
    if not phase.data.get("proof_passed"):
        raise PhaseError("review refused: prove first (no passing proof on record)")
    verdict = reviewer(phase)
    review_id = _events.new_id("rev")
    phase.data["review_verdict"] = {
        "review_id": review_id,
        "verdict": verdict.verdict,
        "notes": verdict.notes,
        "action": verdict.action,
    }
    _emit(
        root,
        "review_recorded",
        phase,
        review_id=review_id,
        payload={"verdict": verdict.verdict, "action": verdict.action},
    )
    return verdict


def _require_proven_and_audited(phase: Phase, step: str) -> None:
    """Later steps ride on a passing proof and a completed audit: a runtime
    trace or a human signature taken before them validates nothing."""
    if not phase.data.get("proof_passed"):
        raise PhaseError("%s refused: prove first (no passing proof on record)" % step)
    need = reviews_required(_phase_level(phase.data))
    if not phase.data.get("audit_passed") or int(phase.data.get("audit_agent_count", 0)) < need:
        raise PhaseError(
            "%s refused: audit first (%d report(s) required)" % (step, need)
        )


def runtime(root: Path, report: str) -> Phase:
    phase = _require_active(root, status="active")
    _require_proven_and_audited(phase, "runtime")
    p = Path(report)
    if not p.exists():
        raise PhaseError("runtime proof not found: %s" % report)
    data = p.read_bytes()
    if len(data) < _MIN_REPORT_BYTES:
        raise PhaseError("runtime proof too short (<%d bytes)" % _MIN_REPORT_BYTES)
    verdict = _parse_report_verdict(data.decode("utf-8", "replace"), report)
    if verdict not in ("PASS", "PASS_WITH_NOTES"):
        raise PhaseError(
            "runtime proof declares VERDICT: %s -- runtime is not proven" % verdict
        )
    phase.data["runtime_passed"] = True
    phase.data["runtime_report"] = str(p.resolve())
    _emit(root, "runtime_recorded", phase)
    return phase


def human_approve(root: Path, *, validator: str) -> Phase:
    """Record the explicit human validation required at level 3.

    The signature is the LAST gate of the level-3 path: it certifies work
    that has already been proven, audited and runtime-checked -- signing
    earlier would certify nothing.
    """
    phase = _require_active(root, status="active")
    if _phase_level(phase.data) != 3:
        raise PhaseError("human validation is reserved for level 3 phases")
    _require_proven_and_audited(phase, "human validation")
    if not (phase.data.get("runtime_passed") and phase.data.get("runtime_report")):
        raise PhaseError("human validation refused: record the runtime proof first")
    if not validator or not validator.strip():
        raise PhaseError("validator is required")
    phase.data["human_validation_passed"] = True
    phase.data["human_validated_at"] = _now()
    phase.data["human_validator"] = validator.strip()
    _emit(root, "human_validation_recorded", phase)
    return phase


def close(
    root: Path,
    *,
    lesson: str,
    commit_sha: Optional[str] = None,
    verifier: Optional[Verifier] = None,
    repo_guard: Optional[RepoGuard] = None,
) -> Phase:
    phase = _require_active(root)
    if not phase.data.get("proof_passed"):
        raise PhaseError("proof absent or failing")
    if not phase.data.get("audit_passed"):
        raise PhaseError("required audit missing")
    if int(phase.data.get("open_exploited", 0)) > 0:
        raise PhaseError("unresolved EXPLOITED findings: close refused")
    # A recorded review verdict gates the close: REFUS means fix first, and
    # REVIEW_UNAVAILABLE means the review did not verifiably happen -- neither
    # may be waved through. No verdict recorded = the optional reviewer was
    # not used; the mandatory audit gate above still applies.
    recorded_review = phase.data.get("review_verdict") or {}
    if recorded_review.get("verdict") in (ReviewVerdict.REFUS, ReviewVerdict.UNAVAILABLE):
        raise PhaseError(
            "review verdict %s: close refused (fix and re-review, or re-run "
            "the review)" % recorded_review["verdict"]
        )

    level = _phase_level(phase.data)
    need = reviews_required(level)
    have = int(phase.data.get("audit_agent_count", 0))
    if have < need:
        raise PhaseError("insufficient audit at close: %d reports required (%d given)" % (need, have))

    if level == 3 and not (
        phase.data.get("runtime_passed") and phase.data.get("runtime_report")
    ):
        raise PhaseError("level 3: runtime not proven (runtime_passed + report required)")
    if level == 3 and not phase.data.get("human_validation_passed"):
        raise PhaseError("level 3: human validation missing (human-approve required)")
    if level >= 2 and not phase.data.get("full_suite_declared"):
        raise PhaseError("level %d: full test suite not declared (full_suite required at init)" % level)
    if phase.data.get("analysis_required") and not (
        (phase.data.get("analysis") or {}).get("used")
    ):
        raise PhaseError("ANALYSIS_REQUIRED: close refused (pre-phase analysis metadata missing)")

    # Belt and braces over the prove-time invalidation above: a validation
    # that somehow predates the last proof (hand-edited or legacy state)
    # reviewed a tree the proof no longer describes.
    proved_at = phase.data.get("proved_at")
    if proved_at:
        stale = []
        if phase.data.get("audited_at") and phase.data["audited_at"] < proved_at:
            stale.append("audit")
        if (phase.data.get("human_validated_at")
                and phase.data["human_validated_at"] < proved_at):
            stale.append("human validation")
        if stale:
            raise PhaseError(
                "stale validation(s) at close: %s recorded before the last "
                "prove -- re-run them against the current proof"
                % ", ".join(stale)
            )

    if not lesson or not lesson.strip():
        raise PhaseError("lesson is required at close (learning loop)")
    phase.data["lesson"] = lesson.strip()

    if commit_sha:
        phase.data["commit_sha"] = commit_sha

    sha = phase.data.get("commit_sha")
    if sha:
        # P28b repo-match guard: the commit must belong to the repo that holds
        # the files_allowed and touch the perimeter. Runs before verify so a
        # commit from an unrelated repo is rejected outright. Only enforced when
        # a commit_sha is present (a commit-less close makes no claim to verify).
        guard = repo_guard or default_repo_guard
        guard(root, phase.files_allowed, sha)
    if sha and phase.data.get("proof_command"):
        verifier = verifier or default_verifier
        if not verifier(root, sha, phase.data["proof_command"]):
            raise PhaseError(
                "independent VERIFY failed: proof does not pass on commit %s "
                "(worktree re-run). Close refused." % sha
            )
        phase.data["verify_passed"] = True

    phase.data["active"] = False
    phase.data["status"] = "complete"
    phase.data["closed_at"] = _now()
    # The v2 event carries a full state snapshot: the journal alone can
    # reconstruct the closed phase (no separate summary line needed).
    _emit(root, "phase_closed", phase)
    return phase


def reset(root: Path) -> None:
    path = state_paths(root)["state"]
    if path.exists():
        # Journal a terminal inactive snapshot before deleting, so the log
        # remains reconstructible even across a reset.
        try:
            phase = load_state(root)
        except (ValueError, OSError):
            phase = None
        if phase is not None:
            phase.data["active"] = False
            phase.data["status"] = "reset"
            _emit(root, "phase_reset", phase)
        path.unlink()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _split_csv(values: Optional[Sequence[str]]) -> List[str]:
    out: List[str] = []
    for v in values or []:
        out.extend(part.strip() for part in v.split(",") if part.strip())
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="phases", description="Phase state machine.")
    parser.add_argument("--root", default=".", help="repository root (default: cwd)")
    sub = parser.add_subparsers(dest="action", required=True)

    p_init = sub.add_parser("init", help="create a pending phase")
    p_init.add_argument("--objective", required=True)
    p_init.add_argument("--files", action="append", required=True, help="file(s); repeatable or comma-separated")
    p_init.add_argument("--proof", required=True, help="proof command")
    p_init.add_argument("--level", type=int, default=None, choices=(0, 1, 2, 3),
                        help="risk level 0-3 (the policy); overrides --audit")
    p_init.add_argument("--audit", default=None, choices=sorted(AUDIT_TO_LEVEL),
                        help="legacy alias: none=0, review=1, security=2, critical=3")
    p_init.add_argument("--full-suite", action="store_true",
                        help="declare the proof covers the whole test suite (required at level >= 2)")
    p_init.add_argument("--inherited-from", default=None)
    p_init.add_argument("--require-analysis", action="store_true",
                        help="require pre-phase analysis metadata (fail-closed)")
    p_init.add_argument("--analysis-context", default="")
    p_init.add_argument("--analysis-depth", default="", choices=("", "core", "deep", "full"))
    p_init.add_argument("--analysis-axes", action="append", default=[],
                        help="axis name(s); repeatable or comma-separated")
    p_init.add_argument("--analysis-ref", default="",
                        help="artifact://<store>/<id> pointing at the stored analysis")

    sub.add_parser("approve", help="approve a pending phase")
    sub.add_parser("prove", help="run the proof command")
    sub.add_parser("status", help="print phase state as JSON")
    sub.add_parser("reset", help="delete phase state")

    p_audit = sub.add_parser("audit", help="record adversarial audit reports")
    p_audit.add_argument("--report", action="append", default=[], help="report path(s)")
    p_audit.add_argument("--open-exploited", type=int, default=0)

    p_rt = sub.add_parser("runtime", help="record a runtime isolation proof")
    p_rt.add_argument("--report", required=True)

    p_ha = sub.add_parser("human-approve", help="record the level-3 human validation")
    p_ha.add_argument("--validator", required=True)

    p_close = sub.add_parser("close", help="close the phase")
    p_close.add_argument("--lesson", required=True)
    p_close.add_argument("--commit-sha", default=None)

    p_prep = sub.add_parser(
        "prepare-analysis",
        help="run a bundled Multidim analysis and print the metadata for init")
    p_prep.add_argument("--subject", required=True, help="what to analyse")
    p_prep.add_argument("--level", type=int, default=2, choices=(0, 1, 2, 3),
                        help="risk level; sets the analysis depth (0/1=core, 2=deep, 3=full)")
    p_prep.add_argument("--context", default="",
                        help="optional: force a Multidim context (else auto-detected)")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    try:
        if args.action == "init":
            init_phase(
                root,
                objective=args.objective,
                files_allowed=_split_csv(args.files),
                proof_command=args.proof,
                audit=args.audit,
                level=args.level,
                full_suite=args.full_suite,
                inherited_from=args.inherited_from,
                require_analysis=args.require_analysis,
                analysis_context=args.analysis_context,
                analysis_depth=args.analysis_depth,
                analysis_axes=args.analysis_axes,
                analysis_ref=args.analysis_ref,
            )
            print("PHASE_PENDING")
        elif args.action == "approve":
            approve(root)
            print("PHASE_APPROVED")
        elif args.action == "prove":
            code = prove(root)
            if code == 0:
                print("PHASE_PROOF_PASSED")
            else:
                print("PHASE_PROOF_FAILED (exit %d)" % code)
            return code
        elif args.action == "audit":
            phase = record_audit(root, reports=args.report, open_exploited=args.open_exploited)
            print("PHASE_AUDIT_RECORDED (%d reports)" % phase.data["audit_agent_count"])
        elif args.action == "runtime":
            runtime(root, args.report)
            print("PHASE_RUNTIME_RECORDED")
        elif args.action == "human-approve":
            human_approve(root, validator=args.validator)
            print("PHASE_HUMAN_APPROVED")
        elif args.action == "close":
            close(root, lesson=args.lesson, commit_sha=args.commit_sha)
            print("PHASE_COMPLETE")
        elif args.action == "status":
            phase = load_state(root)
            print("PHASE_NONE" if phase is None else json.dumps(phase.data, indent=2, ensure_ascii=False))
        elif args.action == "reset":
            reset(root)
            print("PHASE_RESET")
        elif args.action == "prepare-analysis":
            # spawn the bundled Multidim server over stdio, run the analysis,
            # persist the artifact, and print the exact metadata to feed init.
            from .multidim import client as md_client
            try:
                meta = md_client.prepare(
                    args.subject, level=args.level,
                    context=(args.context or None), root=root)
            except md_client.MultidimClientError as exc:
                # fail-closed: no analysis produced, never a silent empty one
                print("ANALYSIS_UNAVAILABLE: %s" % exc, file=sys.stderr)
                return 2
            print(json.dumps(meta, indent=2, ensure_ascii=False))
    except PhaseError as exc:
        print("PHASE_ERROR: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
