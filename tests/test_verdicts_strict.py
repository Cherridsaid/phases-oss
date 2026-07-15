"""Group D: verdicts are a closed vocabulary, parsed strictly.

PASS, PASS_WITH_NOTES, REFUS, EXPLOITED are the only report verdicts.
PASSABLE, BANANA or a bare occurrence of the word VERDICT approve nothing,
and REFUS / EXPLOITED / REVIEW_UNAVAILABLE always block close.
"""

import tempfile
import unittest
from pathlib import Path

from phases_oss import phases
from phases_oss.phases import PhaseError, ReviewVerdict
from phases_oss.reviewers import cloud


def _ok_runner(command, cwd, env=None):
    return 0, "proof ok"


def _report(root, name, body):
    path = Path(root) / name
    path.write_text(body + "\n" + "filler line for the size floor " * 10,
                    encoding="utf-8")
    return str(path)


def _phase_ready_for_audit(root, level=1):
    phases.init_phase(root, objective="t", files_allowed=["a.py"],
                      proof_command="pytest tests/", level=level,
                      full_suite=level >= 2)
    phases.approve(root)
    phases.prove(root, runner=_ok_runner)


class TestCloudParseStrict(unittest.TestCase):
    def test_passable_is_not_a_pass(self):
        v = cloud.parse_response("VERDICT: PASSABLE\nlooks fine to me")
        self.assertEqual(v.verdict, ReviewVerdict.UNAVAILABLE)

    def test_banana_is_unavailable(self):
        v = cloud.parse_response("VERDICT: BANANA")
        self.assertEqual(v.verdict, ReviewVerdict.UNAVAILABLE)

    def test_pass_requires_a_whole_line(self):
        self.assertEqual(cloud.parse_response("VERDICT: PASS").verdict,
                         ReviewVerdict.PASS_WITH_NOTES)
        self.assertEqual(cloud.parse_response("notes\nVERDICT: PASS\nmore").verdict,
                         ReviewVerdict.PASS_WITH_NOTES)
        self.assertEqual(cloud.parse_response("I would say VERDICT: PASS maybe").verdict,
                         ReviewVerdict.UNAVAILABLE)

    def test_refus_and_exploited_always_refuse(self):
        self.assertEqual(cloud.parse_response("VERDICT: REFUS").verdict,
                         ReviewVerdict.REFUS)
        self.assertEqual(cloud.parse_response("one EXPLOITED finding\nVERDICT: PASS").verdict,
                         ReviewVerdict.REFUS)


class TestReportVerdictsStrict(unittest.TestCase):
    def test_bare_verdict_word_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _phase_ready_for_audit(root)
            r = _report(root, "r.md", "the VERDICT of history is unclear")
            with self.assertRaises(PhaseError):
                phases.record_audit(root, reports=[r])
            self.assertFalse(phases.load_state(root).data["audit_passed"])

    def test_unknown_verdict_value_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _phase_ready_for_audit(root)
            for value in ("BANANA", "PASSABLE", "OK"):
                r = _report(root, "r_%s.md" % value, "VERDICT: %s" % value)
                with self.subTest(value=value), self.assertRaises(PhaseError):
                    phases.record_audit(root, reports=[r])

    def test_refus_report_blocks_audit_and_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _phase_ready_for_audit(root)
            r = _report(root, "r.md", "VERDICT: REFUS")
            with self.assertRaises(PhaseError):
                phases.record_audit(root, reports=[r])
            self.assertFalse(phases.load_state(root).data["audit_passed"])
            with self.assertRaises(PhaseError):
                phases.close(root, lesson="refus must block")

    def test_exploited_report_contradicting_caller_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _phase_ready_for_audit(root)
            r = _report(root, "r.md", "VERDICT: EXPLOITED")
            with self.assertRaises(PhaseError) as ctx:
                phases.record_audit(root, reports=[r], open_exploited=0)
            self.assertIn("open_exploited", str(ctx.exception))

    def test_strictest_verdict_wins_in_one_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _phase_ready_for_audit(root)
            r = _report(root, "r.md", "VERDICT: PASS\nlater...\nVERDICT: REFUS")
            with self.assertRaises(PhaseError):
                phases.record_audit(root, reports=[r])

    def test_pass_report_still_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _phase_ready_for_audit(root)
            r = _report(root, "r.md", "VERDICT: PASS")
            phases.record_audit(root, reports=[r])
            self.assertTrue(phases.load_state(root).data["audit_passed"])


class TestRuntimeVerdictStrict(unittest.TestCase):
    def test_runtime_refus_is_not_proven(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _phase_ready_for_audit(root, level=3)
            phases.record_audit(root, reports=[_report(root, "a0.md", "VERDICT: PASS")])
            r = _report(root, "rt.md", "VERDICT: REFUS")
            with self.assertRaises(PhaseError):
                phases.runtime(root, r)
            self.assertFalse(phases.load_state(root).data["runtime_passed"])

    def test_runtime_pass_is_proven(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _phase_ready_for_audit(root, level=3)
            phases.record_audit(root, reports=[_report(root, "a0.md", "VERDICT: PASS")])
            phases.runtime(root, _report(root, "rt.md", "VERDICT: PASS"))
            self.assertTrue(phases.load_state(root).data["runtime_passed"])


class TestCloseGatedOnReviewVerdict(unittest.TestCase):
    def _reviewer(self, verdict):
        return lambda phase: ReviewVerdict(verdict, "notes")

    def _ready_to_close(self, root):
        _phase_ready_for_audit(root)
        phases.record_audit(root, reports=[_report(root, "r.md", "VERDICT: PASS")])

    def test_refus_review_blocks_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._ready_to_close(root)
            phases.review(root, self._reviewer(ReviewVerdict.REFUS))
            with self.assertRaises(PhaseError) as ctx:
                phases.close(root, lesson="refus blocks")
            self.assertIn("REFUS", str(ctx.exception))

    def test_unavailable_review_blocks_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._ready_to_close(root)
            phases.review(root, self._reviewer(ReviewVerdict.UNAVAILABLE))
            with self.assertRaises(PhaseError):
                phases.close(root, lesson="unavailable blocks")

    def test_pass_review_allows_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._ready_to_close(root)
            phases.review(root, self._reviewer(ReviewVerdict.PASS_WITH_NOTES))
            phases.close(root, lesson="pass with notes closes")
            self.assertEqual(phases.load_state(root).data["status"], "complete")


if __name__ == "__main__":
    unittest.main()
