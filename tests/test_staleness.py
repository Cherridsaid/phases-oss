"""Group C: a new prove invalidates every earlier validation.

An audit, review verdict, runtime proof or human validation recorded before
a re-prove examined a tree that no longer exists; close must never accept
them against the new proof.
"""

import tempfile
import unittest
from pathlib import Path

from phases_oss import phases
from phases_oss.phases import PhaseError


def _ok_runner(command, cwd, env=None):
    return 0, "proof ok"


def _report(root, name, filler, verdict="PASS"):
    path = Path(root) / name
    path.write_text("VERDICT: %s\n" % verdict + filler * 40, encoding="utf-8")
    return str(path)


class TestReproveInvalidatesValidations(unittest.TestCase):
    def _phase_level1(self, root):
        phases.init_phase(root, objective="t", files_allowed=["a.py"],
                          proof_command="pytest tests/", level=1)
        phases.approve(root)

    def test_reprove_invalidates_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._phase_level1(root)
            phases.prove(root, runner=_ok_runner)
            phases.record_audit(root, reports=[_report(root, "r.md", "finding ")])
            self.assertTrue(phases.load_state(root).data["audit_passed"])
            # the tree changes; the proof is re-run
            phases.prove(root, runner=_ok_runner)
            state = phases.load_state(root)
            self.assertFalse(state.data["audit_passed"],
                             "an audit of the previous tree must not survive a re-prove")
            self.assertEqual(state.data["audit_agent_count"], 0)
            with self.assertRaises(PhaseError):
                phases.close(root, lesson="stale audit must block")

    def test_reprove_invalidates_runtime_and_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._phase_level1(root)
            phases.prove(root, runner=_ok_runner)
            phases.record_audit(root, reports=[_report(root, "a0.md", "audit first ")])
            phases.runtime(root, _report(root, "rt.md", "runtime trace "))
            self.assertTrue(phases.load_state(root).data["runtime_passed"])
            phases.prove(root, runner=_ok_runner)
            state = phases.load_state(root)
            self.assertFalse(state.data["runtime_passed"])
            self.assertIsNone(state.data["review_verdict"])

    def test_reprove_invalidates_human_validation_level3(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            phases.init_phase(root, objective="t", files_allowed=["a.py"],
                              proof_command="pytest tests/", level=3,
                              full_suite=True)
            phases.approve(root)
            phases.prove(root, runner=_ok_runner)
            phases.record_audit(root, reports=[_report(root, "a0.md", "audit first ")])
            phases.runtime(root, _report(root, "rt0.md", "runtime trace "))
            phases.human_approve(root, validator="said")
            self.assertTrue(phases.load_state(root).data["human_validation_passed"])
            phases.prove(root, runner=_ok_runner)
            state = phases.load_state(root)
            self.assertFalse(state.data["human_validation_passed"])
            self.assertIsNone(state.data["human_validator"])

    def test_level0_audit_gate_survives_reprove(self):
        # Level 0 requires no review: re-proving must not wedge the phase.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            phases.init_phase(root, objective="t", files_allowed=["a.py"],
                              proof_command="pytest tests/", level=0)
            phases.approve(root)
            phases.prove(root, runner=_ok_runner)
            phases.prove(root, runner=_ok_runner)
            self.assertTrue(phases.load_state(root).data["audit_passed"])
            phases.close(root, lesson="level 0 flow intact")

    def test_close_rejects_audit_older_than_proof(self):
        # Defence in depth: even a hand-edited state with a stale timestamp
        # must not close.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._phase_level1(root)
            phases.prove(root, runner=_ok_runner)
            phases.record_audit(root, reports=[_report(root, "r.md", "finding ")])
            state = phases.load_state(root)
            state.data["audited_at"] = "2000-01-01T00:00:00+00:00"
            phases.save_state(root, state)
            with self.assertRaises(PhaseError) as ctx:
                phases.close(root, lesson="stale timestamp")
            self.assertIn("stale", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
