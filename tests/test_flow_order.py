"""The declared path is MANDATORY: init -> approve -> prove -> audit/review
-> runtime/human-approve (level 3) -> close.

Every step must refuse a phase that has not completed the previous steps,
and a refused call must never mutate the phase (in particular: never
activate it). A closed or explicitly abandoned phase unblocks a new init.
"""

import json
import tempfile
import unittest
from pathlib import Path

from phases_oss import phases
from phases_oss.phases import PhaseError, ReviewVerdict


def _ok_runner(command, cwd, env=None):
    return 0, "proof ok"


def _report(root, name, verdict="PASS", filler="finding "):
    path = Path(root) / name
    path.write_text("VERDICT: %s\n" % verdict + filler * 40, encoding="utf-8")
    return str(path)


def _pass_reviewer(phase):
    return ReviewVerdict(ReviewVerdict.PASS, "ok")


class FlowBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _init(self, level=1, **kw):
        kw.setdefault("objective", "flow")
        kw.setdefault("files_allowed", ["a.py"])
        kw.setdefault("proof_command", "pytest tests/")
        if level >= 2:
            kw.setdefault("full_suite", True)
        return phases.init_phase(self.root, level=level, **kw)

    def _status(self):
        return phases.load_state(self.root).data["status"]


class TestStepsRefuseBeforeApprove(FlowBase):
    """Steps 2-5 of the mandated minimal tests: nothing runs, and nothing
    activates, before approve()."""

    def test_audit_before_approve_refused_and_not_activated(self):
        self._init(level=1)
        with self.assertRaises(PhaseError):
            phases.record_audit(self.root, reports=[_report(self.root, "r.md")])
        self.assertEqual(self._status(), "pending_approval")

    def test_review_before_approve_refused_and_not_activated(self):
        self._init(level=1)
        with self.assertRaises(PhaseError):
            phases.review(self.root, _pass_reviewer)
        self.assertEqual(self._status(), "pending_approval")

    def test_runtime_before_approve_refused_and_not_activated(self):
        self._init(level=3)
        with self.assertRaises(PhaseError):
            phases.runtime(self.root, _report(self.root, "rt.md"))
        self.assertEqual(self._status(), "pending_approval")

    def test_human_approve_before_approve_refused_and_not_activated(self):
        self._init(level=3)
        with self.assertRaises(PhaseError):
            phases.human_approve(self.root, validator="said")
        self.assertEqual(self._status(), "pending_approval")

    def test_close_before_approve_refused(self):
        self._init(level=0)
        with self.assertRaises(PhaseError):
            phases.close(self.root, lesson="too early")
        self.assertEqual(self._status(), "pending_approval")


class TestStepsRefuseBeforeProve(FlowBase):
    """review / runtime / human-approve all need a passing proof first."""

    def test_review_refuses_unproven_phase(self):
        self._init(level=1)
        phases.approve(self.root)
        with self.assertRaises(PhaseError):
            phases.review(self.root, _pass_reviewer)
        self.assertIsNone(phases.load_state(self.root).data["review_verdict"])

    def test_review_refuses_failing_proof(self):
        self._init(level=1)
        phases.approve(self.root)
        phases.prove(self.root, runner=lambda c, w, env=None: (1, "boom"))
        with self.assertRaises(PhaseError):
            phases.review(self.root, _pass_reviewer)

    def test_runtime_refuses_unproven_phase(self):
        self._init(level=3)
        phases.approve(self.root)
        with self.assertRaises(PhaseError):
            phases.runtime(self.root, _report(self.root, "rt.md"))
        self.assertFalse(phases.load_state(self.root).data["runtime_passed"])

    def test_human_approve_refuses_unproven_phase(self):
        self._init(level=3)
        phases.approve(self.root)
        with self.assertRaises(PhaseError):
            phases.human_approve(self.root, validator="said")
        self.assertFalse(
            phases.load_state(self.root).data["human_validation_passed"]
        )


class TestStepsRefuseBeforeAudit(FlowBase):
    """runtime and human-approve (level 3) also need the audit done."""

    def _proven_level3(self):
        self._init(level=3)
        phases.approve(self.root)
        phases.prove(self.root, runner=_ok_runner)

    def test_runtime_refuses_insufficiently_audited_phase(self):
        self._proven_level3()
        with self.assertRaises(PhaseError):
            phases.runtime(self.root, _report(self.root, "rt.md"))
        self.assertFalse(phases.load_state(self.root).data["runtime_passed"])

    def test_human_approve_refuses_before_audit(self):
        self._proven_level3()
        with self.assertRaises(PhaseError):
            phases.human_approve(self.root, validator="said")

    def test_human_approve_refuses_before_runtime(self):
        self._proven_level3()
        phases.record_audit(self.root, reports=[_report(self.root, "a.md")])
        with self.assertRaises(PhaseError):
            phases.human_approve(self.root, validator="said")

    def test_full_level3_order_succeeds(self):
        self._proven_level3()
        phases.record_audit(self.root, reports=[_report(self.root, "a.md")])
        phases.runtime(self.root, _report(self.root, "rt.md"))
        phases.human_approve(self.root, validator="said")
        phases.close(self.root, lesson="full ordered path")
        self.assertEqual(self._status(), "complete")


class TestTerminalStatesUnblockInit(FlowBase):
    """Minimal test 6: a closed or explicitly abandoned phase allows a new
    init, and abandonment leaves a terminal event in the journal."""

    def test_new_init_after_close(self):
        self._init(level=0)
        phases.approve(self.root)
        phases.prove(self.root, runner=_ok_runner)
        phases.close(self.root, lesson="done")
        self._init(level=0, objective="next phase")
        self.assertEqual(
            phases.load_state(self.root).data["objective"], "next phase"
        )

    def test_new_init_after_reset_with_terminal_event(self):
        self._init(level=1, objective="abandoned work")
        phases.approve(self.root)
        phases.reset(self.root)
        log = (self.root / ".claude" / "phase-log.jsonl").read_text(encoding="utf-8")
        events = [json.loads(line) for line in log.strip().splitlines()]
        terminal = [e for e in events if e["event_type"] == "phase_reset"]
        self.assertTrue(terminal, "reset must journal a terminal event")
        self.assertEqual(
            terminal[-1]["payload"]["state"]["objective"], "abandoned work"
        )
        self._init(level=0, objective="fresh start")
        self.assertEqual(
            phases.load_state(self.root).data["objective"], "fresh start"
        )


if __name__ == "__main__":
    unittest.main()
