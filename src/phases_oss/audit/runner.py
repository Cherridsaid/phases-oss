"""The orchestrator: visits all 71 phases, executes the applicable ones.

The loop is deterministic and owns the control flow. It decides *nothing* about
order -- that is frozen in :mod:`registry` -- and it never lets a model choose
what comes next. For each ordinal it applies the same fixed cascade of gates,
and whichever gate fires first is recorded with its typed reason::

    body absent          -> missing_skill    / skill_body_absent
    not in the matrix    -> not_applicable   / signal_absent:<name>
    licence not given    -> skipped_license  / license_not_confirmed
    needs the target run -> not_applicable   / policy_static_only
    nothing to work on   -> not_applicable   / no_findings_to_process
    tool not installed   -> skipped_offline  / tool_absent
    no model wired       -> degraded         / model_plane_unavailable
    otherwise            -> completed | failed

Exactly one stage exists at a time: it is built before the phase, exposes one
``SKILL.md``, and is destroyed before the next ordinal begins.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence

from . import guard, router, tools
from .overlay import SkillStage
from .registry import (
    LICENSE_GATED,
    ORDINALS,
    PHASE_COUNT,
    PLANE_EXECUTION,
    PhaseSpec,
    pipeline_manifest,
    resolve_all,
)
from .runstate import (
    COMPLETED,
    DEGRADED,
    FAILED,
    MISSING_SKILL,
    NOT_APPLICABLE,
    RunState,
    SKIPPED_LICENSE,
    SKIPPED_OFFLINE,
    run_dir,
)

POLICY_STATIC_ONLY = "static_only"
POLICY_LOCAL_EXECUTION = "local_test_execution"


class PhaseOutcome(NamedTuple):
    status: str
    reason: str
    artifacts: Sequence[str] = ()
    note: Optional[str] = None


#: A model-plane adapter takes the phase spec and the built stage, and returns
#: an outcome. Returning ``None`` means "no model available", which the loop
#: records as ``degraded`` rather than inventing an analysis.
Adapter = Callable[[PhaseSpec, SkillStage, Path], Optional[PhaseOutcome]]


def null_adapter(spec: PhaseSpec, stage: SkillStage, target: Path) -> Optional[PhaseOutcome]:
    """Default model plane: none wired in. Honest ``degraded``, never a fake pass."""
    return None


class SubprocessAdapter:
    """Run a local agent CLI against the stage, one skill visible.

    The child inherits the stage's ``HOME`` (so it discovers exactly one skill)
    but keeps its provider credentials -- it *is* the model plane, and the model
    plane is the only process allowed an egress. Execution-plane tools get the
    scrubbed environment instead; the two must never share one environment.
    """

    def __init__(self, argv: Sequence[str], *, timeout: int = 900):
        self.argv = list(argv)
        self.timeout = timeout

    def __call__(self, spec: PhaseSpec, stage: SkillStage, target: Path) -> Optional[PhaseOutcome]:
        if stage.root is None:
            return None
        out = stage.root / "phase-output.txt"
        argv = [a.replace("{skill}", spec.skill).replace("{target}", str(target)) for a in self.argv]
        try:
            proc = subprocess.run(
                argv,
                cwd=str(target),
                env=stage.env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return PhaseOutcome(DEGRADED, "model_plane_unavailable", note=str(exc))
        out.write_text(proc.stdout or "", encoding="utf-8")
        status = COMPLETED if proc.returncode == 0 else FAILED
        reason = "selected" if proc.returncode == 0 else "execution_error"
        return PhaseOutcome(status, reason, artifacts=(str(out),), note="exit %d" % proc.returncode)


class AuditRunner:
    """Drives one run from PHASE 01 to PHASE 71."""

    def __init__(
        self,
        target: Path,
        *,
        root: Optional[Path] = None,
        policy: str = POLICY_STATIC_ONLY,
        codeql_license_confirmed: bool = False,
        adapter: Adapter = null_adapter,
        skill_roots: Optional[Sequence[Path]] = None,
        stage_parent: Optional[Path] = None,
    ):
        self.target = Path(target).resolve()
        # Run artifacts live under the *orchestrator's* root, never inside the
        # audited tree: writing there would break the read-only guarantee.
        self.root = Path(root).resolve() if root else Path.cwd().resolve()
        if self.root == self.target or self.target in self.root.parents:
            raise ValueError(
                "run root %s is inside the audited target %s: artifacts would "
                "mutate the tree the run promises to leave read-only"
                % (self.root, self.target)
            )
        self.policy = policy
        self.codeql_license_confirmed = bool(codeql_license_confirmed)
        self.adapter = adapter
        self.skill_roots = list(skill_roots) if skill_roots is not None else None
        self.stage_parent = Path(stage_parent) if stage_parent else None
        self._resolutions = {r.spec.ordinal: r for r in resolve_all(self.skill_roots)}
        self._findings_available = False

    # -- gates ------------------------------------------------------------ #
    def _gate(self, spec: PhaseSpec, matrix: Dict[str, Dict]) -> Optional[PhaseOutcome]:
        """First failing gate for ``spec``, or None when the phase should run."""
        resolution = self._resolutions[spec.ordinal]
        if resolution.missing:
            return PhaseOutcome(MISSING_SKILL, "skill_body_absent",
                                note="no SKILL.md found for %r" % spec.skill)

        entry = matrix.get("%02d" % spec.ordinal, {})
        if entry.get("decision") == router.NOT_APPLICABLE:
            return PhaseOutcome(
                NOT_APPLICABLE, "signal_absent:%s" % (entry.get("missing_signal") or spec.signal)
            )

        if spec.skill in LICENSE_GATED and not self.codeql_license_confirmed:
            return PhaseOutcome(SKIPPED_LICENSE, "license_not_confirmed")

        if spec.requires_execution and self.policy == POLICY_STATIC_ONLY:
            return PhaseOutcome(NOT_APPLICABLE, "policy_static_only")

        if spec.signal in router.RUNTIME_SIGNALS and not self._findings_available:
            return PhaseOutcome(NOT_APPLICABLE, "no_findings_to_process")

        return None

    # -- execution -------------------------------------------------------- #
    def _run_execution_plane(
        self, spec: PhaseSpec, stage: SkillStage, artifacts_dir: Path
    ) -> PhaseOutcome:
        if not tools.is_available(spec.skill):
            return PhaseOutcome(SKIPPED_OFFLINE, "tool_absent",
                                note="no local executable for %r" % spec.skill)
        tool_spec = tools.TOOLS[spec.skill]
        if tool_spec.internal:
            # Served by this package; wiring lands with the phases that consume
            # it. Recorded degraded rather than completed: nothing ran yet.
            return PhaseOutcome(DEGRADED, "model_plane_unavailable",
                                note="internal handler not wired for %r" % spec.skill)
        out = artifacts_dir / ("%02d-%s.json" % (spec.ordinal, spec.skill))
        rules = tools.semgrep_rules() if spec.skill == "semgrep" else None
        try:
            argv = tools.build_command(spec.skill, target=self.target, out=out, rules=rules)
        except FileNotFoundError as exc:
            # The binary exists but cannot work offline (no local rule pack).
            # Reaching the network to fetch one is not an option, so the phase
            # is skipped and says why.
            return PhaseOutcome(SKIPPED_OFFLINE, "tool_absent", note=str(exc))
        env = guard.execution_env()
        guard.assert_no_secrets(env)
        try:
            proc = subprocess.run(
                argv,
                cwd=str(self.target),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
                timeout=1800,
            )
        except OSError as exc:
            # The tool could not be launched at all (missing, or refused by an
            # application-control policy). That is "unavailable here", not "ran
            # and broke" -- classifying it as a failure would send the user
            # hunting for a bug in their own code.
            return PhaseOutcome(SKIPPED_OFFLINE, "tool_absent",
                                note="cannot launch %s: %s" % (spec.skill, exc))
        except subprocess.TimeoutExpired as exc:
            return PhaseOutcome(FAILED, "execution_error", note=str(exc))
        produced = [str(out)] if out.exists() else []
        if produced:
            self._findings_available = True
        # Scanners exit non-zero when they *find* something; that is a
        # successful run, not a failure. Only a missing artifact is a failure.
        status = COMPLETED if produced or proc.returncode == 0 else FAILED
        reason = "selected" if status == COMPLETED else "execution_error"
        note = "exit %d" % proc.returncode
        if spec.skill == "semgrep":
            stats = tools.rule_stats(rules, proc.stdout or "")
            note = "%s | rules %s" % (note, json.dumps(stats))
        if status == FAILED:
            # Keep the tool's own last words: "execution_error" alone is not
            # actionable, and this is the line that says what actually broke.
            tail = (proc.stdout or "").strip().splitlines()[-3:]
            note = "%s | %s" % (note, " / ".join(tail)) if tail else note
        return PhaseOutcome(status, reason, artifacts=produced, note=note)

    def _run_phase(self, spec: PhaseSpec, artifacts_dir: Path) -> PhaseOutcome:
        resolution = self._resolutions[spec.ordinal]
        with SkillStage(spec, resolution.path, parent=self.stage_parent) as stage:
            if spec.plane == PLANE_EXECUTION:
                return self._run_execution_plane(spec, stage, artifacts_dir)
            outcome = self.adapter(spec, stage, self.target)
            if outcome is None:
                return PhaseOutcome(DEGRADED, "model_plane_unavailable",
                                    note="no model-plane adapter wired")
            return outcome

    # -- driver ----------------------------------------------------------- #
    def run(self, *, resume_from: Optional[str] = None) -> RunState:
        """Execute the pipeline; returns the final run state."""
        if resume_from:
            state = RunState.load(self.root, resume_from)
        else:
            state = RunState.create(
                target=str(self.target),
                policy=self.policy,
                codeql_license_confirmed=self.codeql_license_confirmed,
            )
            state.save(self.root, event="run.created")
            state.freeze_applicability(router.build_matrix(self.target), root=self.root)

        directory = run_dir(self.root, state.run_id)
        artifacts_dir = directory / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (directory / "pipeline.json").write_text(
            json.dumps(pipeline_manifest(self.skill_roots), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (directory / "guards.json").write_text(
            json.dumps(guard.audit_env_report(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        matrix = state.data.get("applicability") or {}
        with guard.ReadOnlyTarget(self.target):
            while True:
                ordinal = state.next_ordinal()
                if ordinal is None:
                    break
                spec = ORDINALS[ordinal - 1]
                state.start(ordinal, root=self.root)
                started = time.time()
                outcome = self._gate(spec, matrix)
                if outcome is None:
                    try:
                        outcome = self._run_phase(spec, artifacts_dir)
                    except Exception as exc:  # noqa: BLE001 - one phase must not kill the run
                        outcome = PhaseOutcome(FAILED, "execution_error", note=repr(exc))
                note = outcome.note
                elapsed = "%.2fs" % (time.time() - started)
                note = "%s | %s" % (note, elapsed) if note else elapsed
                state.finish(
                    ordinal,
                    outcome.status,
                    reason=outcome.reason,
                    artifacts=outcome.artifacts,
                    note=note,
                    root=self.root,
                )
        return state


def report(state: RunState) -> Dict:
    """Machine-readable end-of-run report: every ordinal, always."""
    return {
        "schema": "phases-oss/audit-report/1",
        "run_id": state.run_id,
        "target": state.data["target"],
        "policy": state.data["policy"],
        "phase_count": PHASE_COUNT,
        "summary": state.summary(),
        "phases": [
            {
                "ordinal": e["ordinal"],
                "skill": e["skill"],
                "status": e["status"],
                "reason": e["reason"],
                "note": e["note"],
                "artifacts": e["artifacts"],
            }
            for e in state.phases
        ],
    }
