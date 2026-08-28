"""Run state for the 71-phase pipeline: strict order, one RUNNING, resumable.

A run visits ordinals 1..71 in order. ``start`` refuses any ordinal that is not
the next pending one, so ``PHASE N+1`` cannot begin before ``PHASE N`` reached a
terminal status. Exactly one phase may be RUNNING at a time.

A phase never disappears. When it does not apply, it still ends with a terminal
status and a *typed* reason: a free-text excuse cannot be checked by a test, so
the vocabulary is closed (see ``REASONS`` / ``SIGNAL_ABSENT_PREFIX``).

Persistence mirrors the phase engine's conventions: an atomic state file plus an
append-only JSONL journal, both under ``<root>/.claude/audit/<run_id>/``. State
is written on every transition, so a killed process resumes at the interrupted
ordinal rather than at 1.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .registry import ORDINALS, PHASE_COUNT

STATE_FILENAME = "run-state.json"
JOURNAL_FILENAME = "journal.jsonl"
SCHEMA = "phases-oss/audit-run/1"

PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
NOT_APPLICABLE = "not_applicable"
DEGRADED = "degraded"
FAILED = "failed"
SKIPPED_LICENSE = "skipped_license"
SKIPPED_OFFLINE = "skipped_offline"
MISSING_SKILL = "missing_skill"

#: Statuses a phase may hold once it is done. A run is complete when every
#: ordinal holds one of these.
TERMINAL = frozenset(
    {COMPLETED, NOT_APPLICABLE, DEGRADED, FAILED, SKIPPED_LICENSE, SKIPPED_OFFLINE, MISSING_SKILL}
)
_ALL_STATUSES = TERMINAL | {PENDING, RUNNING}

#: Closed reason vocabulary. ``signal_absent:<name>`` is the one parameterised
#: form (the router reports *which* target property was missing).
SIGNAL_ABSENT_PREFIX = "signal_absent:"
REASONS = frozenset(
    {
        "policy_static_only",
        "tool_absent",
        "license_not_confirmed",
        "skill_body_absent",
        "model_plane_unavailable",
        "execution_error",
        "no_findings_to_process",
        "selected",
    }
)


class RunStateError(Exception):
    """Raised when a transition would break the pipeline's invariants."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def valid_reason(reason: str) -> bool:
    if reason in REASONS:
        return True
    return reason.startswith(SIGNAL_ABSENT_PREFIX) and len(reason) > len(SIGNAL_ABSENT_PREFIX)


def new_run_id() -> str:
    return "run_%s" % uuid.uuid4().hex[:16]


def run_dir(root: Path, run_id: str) -> Path:
    return Path(root) / ".claude" / "audit" / run_id


class RunState:
    """The 71 ordinals plus their live status. Order is never recomputed."""

    def __init__(self, data: Dict):
        self.data = data

    # -- construction ----------------------------------------------------- #
    @classmethod
    def create(
        cls,
        *,
        target: str,
        policy: str = "static_only",
        run_id: Optional[str] = None,
        codeql_license_confirmed: bool = False,
    ) -> "RunState":
        return cls(
            {
                "schema": SCHEMA,
                "run_id": run_id or new_run_id(),
                "target": str(target),
                "policy": policy,
                "codeql_license_confirmed": bool(codeql_license_confirmed),
                "created_at": _now(),
                "finished_at": None,
                "applicability": {},  # frozen once by the router
                "phases": [
                    {
                        "ordinal": spec.ordinal,
                        "skill": spec.skill,
                        "group": spec.group,
                        "plane": spec.plane,
                        "status": PENDING,
                        "reason": None,
                        "started_at": None,
                        "ended_at": None,
                        "artifacts": [],
                        "note": None,
                    }
                    for spec in ORDINALS
                ],
            }
        )

    # -- accessors -------------------------------------------------------- #
    @property
    def run_id(self) -> str:
        return self.data["run_id"]

    @property
    def phases(self) -> List[Dict]:
        return self.data["phases"]

    def phase(self, ordinal: int) -> Dict:
        if not 1 <= int(ordinal) <= PHASE_COUNT:
            raise RunStateError("ordinal %r outside 1..%d" % (ordinal, PHASE_COUNT))
        return self.phases[int(ordinal) - 1]

    def running(self) -> Optional[Dict]:
        for entry in self.phases:
            if entry["status"] == RUNNING:
                return entry
        return None

    def next_ordinal(self) -> Optional[int]:
        """Ordinal of the phase to run next, or None when the run is over.

        A RUNNING phase (an interrupted run) is the one to resume, so it is
        returned before any later pending phase.
        """
        for entry in self.phases:
            if entry["status"] in (PENDING, RUNNING):
                return int(entry["ordinal"])
        return None

    def is_complete(self) -> bool:
        return all(e["status"] in TERMINAL for e in self.phases)

    # -- transitions ------------------------------------------------------ #
    def start(self, ordinal: int, root: Optional[Path] = None) -> Dict:
        """Mark ``ordinal`` RUNNING. Refuses skips, reorderings and overlaps."""
        entry = self.phase(ordinal)
        current = self.running()
        if current is not None and int(current["ordinal"]) != int(ordinal):
            raise RunStateError(
                "PHASE %02d is already RUNNING; only one phase may run at a time"
                % int(current["ordinal"])
            )
        expected = self.next_ordinal()
        if expected is None:
            raise RunStateError("run is complete; no phase left to start")
        if int(ordinal) != expected:
            raise RunStateError(
                "out-of-order start: PHASE %02d requested but PHASE %02d is next "
                "(order is immutable; PHASE N+1 cannot begin before PHASE N ends)"
                % (int(ordinal), expected)
            )
        if entry["status"] in TERMINAL:
            raise RunStateError(
                "PHASE %02d already ended with status %r" % (int(ordinal), entry["status"])
            )
        entry["status"] = RUNNING
        entry["started_at"] = entry["started_at"] or _now()
        if root is not None:
            self.save(root, event="phase.started", ordinal=int(ordinal))
        return entry

    def finish(
        self,
        ordinal: int,
        status: str,
        *,
        reason: str = "selected",
        artifacts: Sequence[str] = (),
        note: Optional[str] = None,
        root: Optional[Path] = None,
    ) -> Dict:
        """Give ``ordinal`` a terminal status and a typed reason."""
        if status not in TERMINAL:
            raise RunStateError(
                "%r is not a terminal status (allowed: %s)"
                % (status, ", ".join(sorted(TERMINAL)))
            )
        if not valid_reason(reason):
            raise RunStateError(
                "untyped reason %r: use one of %s or %s<name>"
                % (reason, ", ".join(sorted(REASONS)), SIGNAL_ABSENT_PREFIX)
            )
        entry = self.phase(ordinal)
        if entry["status"] in TERMINAL:
            raise RunStateError(
                "PHASE %02d already ended with status %r" % (int(ordinal), entry["status"])
            )
        entry["status"] = status
        entry["reason"] = reason
        entry["ended_at"] = _now()
        entry["artifacts"] = [str(a) for a in artifacts]
        entry["note"] = note
        if self.is_complete():
            self.data["finished_at"] = _now()
        if root is not None:
            self.save(root, event="phase.finished", ordinal=int(ordinal))
        return entry

    def freeze_applicability(self, matrix: Dict[str, Dict], root: Optional[Path] = None) -> None:
        """Record the router's decisions once. A second call is refused.

        The matrix is the run's fixed truth: letting a later phase rewrite it
        would make the run unreproducible and would let one bad detection be
        silently patched instead of reported.
        """
        if self.data.get("applicability"):
            raise RunStateError("applicability matrix is already frozen for this run")
        self.data["applicability"] = matrix
        if root is not None:
            self.save(root, event="applicability.frozen")

    # -- persistence ------------------------------------------------------ #
    def save(self, root: Path, *, event: str = "state.saved", ordinal: Optional[int] = None) -> None:
        directory = run_dir(root, self.run_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / STATE_FILENAME
        tmp = target.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(self.data, indent=2, ensure_ascii=False))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        record = {
            "schema": SCHEMA,
            "run_id": self.run_id,
            "timestamp_utc": _now(),
            "event": event,
            "ordinal": ordinal,
            "status": self.phase(ordinal)["status"] if ordinal else None,
            "reason": self.phase(ordinal)["reason"] if ordinal else None,
        }
        with (directory / JOURNAL_FILENAME).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    @classmethod
    def load(cls, root: Path, run_id: str) -> "RunState":
        path = run_dir(root, run_id) / STATE_FILENAME
        if not path.exists():
            raise RunStateError("no run state for %r under %s" % (run_id, root))
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RunStateError("run state is corrupted (%s): %s" % (exc.__class__.__name__, path))
        if not isinstance(data, dict) or len(data.get("phases", [])) != PHASE_COUNT:
            raise RunStateError("run state does not hold %d phases: %s" % (PHASE_COUNT, path))
        # A hand-edited or truncated file must not silently redefine the order.
        for spec, entry in zip(ORDINALS, data["phases"]):
            if int(entry.get("ordinal", -1)) != spec.ordinal or entry.get("skill") != spec.skill:
                raise RunStateError(
                    "run state disagrees with the canonical pipeline at PHASE %02d"
                    % spec.ordinal
                )
            if entry.get("status") not in _ALL_STATUSES:
                raise RunStateError(
                    "PHASE %02d holds unknown status %r" % (spec.ordinal, entry.get("status"))
                )
        return cls(data)

    # -- reporting -------------------------------------------------------- #
    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for entry in self.phases:
            counts[entry["status"]] = counts.get(entry["status"], 0) + 1
        return counts
