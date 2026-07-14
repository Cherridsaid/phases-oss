"""Tests for the static local reviewer (no LLM, no network)."""

import tempfile
import unittest
from pathlib import Path

from phases_oss.phases import ReviewVerdict
from phases_oss.reviewers import LocalReviewer, Severity, get_reviewer, scan_text
from phases_oss.reviewers.local import DEFAULT_RULES


class TestScanText(unittest.TestCase):
    def test_clean_text_no_findings(self):
        self.assertEqual(scan_text("x = 1\nreturn x\n", "f.py"), [])

    def test_hardcoded_secret_is_error(self):
        text = 'api_key = "abcdef123456"\n'
        findings = scan_text(text, "f.py")
        self.assertTrue(any(f.rule == "hardcoded-secret" and f.severity == Severity.ERROR for f in findings))

    def test_breakpoint_is_error(self):
        findings = scan_text("    breakpoint()\n", "f.py")
        self.assertTrue(any(f.rule == "debugger" for f in findings))

    def test_bare_except_is_warn(self):
        findings = scan_text("try:\n    pass\nexcept:\n    pass\n", "f.py")
        warns = [f for f in findings if f.rule == "bare-except"]
        self.assertEqual(len(warns), 1)
        self.assertEqual(warns[0].severity, Severity.WARN)

    def test_todo_is_note(self):
        findings = scan_text("# TODO: later\n", "f.py")
        self.assertTrue(any(f.rule == "todo" and f.severity == Severity.NOTE for f in findings))

    def test_ignore_marker_skips_line(self):
        text = 'password = "supersecret"  # phases-oss: allow reviewed\n'
        self.assertEqual(scan_text(text, "f.py"), [])

    def test_line_numbers_are_one_based(self):
        findings = scan_text("ok = 1\nbreakpoint()\n", "f.py")
        self.assertEqual(findings[0].line, 2)


class TestVerdict(unittest.TestCase):
    def test_no_findings_pass(self):
        v = scan_to_verdict("clean = True\n")
        self.assertEqual(v.verdict, ReviewVerdict.PASS)

    def test_warn_only_pass_with_notes(self):
        v = scan_to_verdict("except:\n    pass\n")
        self.assertEqual(v.verdict, ReviewVerdict.PASS_WITH_NOTES)
        self.assertTrue(v.passed)

    def test_error_refus_with_action(self):
        v = scan_to_verdict('token = "0123456789abcdef"\n')
        self.assertEqual(v.verdict, ReviewVerdict.REFUS)
        self.assertFalse(v.passed)
        self.assertIn("fix", v.action)


class TestLocalReviewer(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel, body):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        return rel

    def test_reviews_files_relative_to_root(self):
        self._write("clean.py", "value = 42\n")
        self._write("bad.py", "breakpoint()\n")

        class FakePhase:
            files_allowed = ["clean.py", "bad.py"]

        reviewer = LocalReviewer(root=self.root)
        verdict = reviewer.review(FakePhase())
        self.assertEqual(verdict.verdict, ReviewVerdict.REFUS)

    def test_missing_file_skipped(self):
        class FakePhase:
            files_allowed = ["does-not-exist.py"]

        reviewer = LocalReviewer(root=self.root)
        self.assertEqual(reviewer.review(FakePhase()).verdict, ReviewVerdict.PASS)


class TestRegistry(unittest.TestCase):
    def test_default_is_local(self):
        self.assertIsInstance(get_reviewer(), LocalReviewer)
        self.assertIsInstance(get_reviewer("local"), LocalReviewer)

    def test_unknown_raises(self):
        with self.assertRaises(ValueError):
            get_reviewer("definitely-not-a-reviewer")

    def test_no_llm_rule_names(self):
        # Guard: the static reviewer must never grow an LLM/Ollama backend.
        names = " ".join(r.name for r in DEFAULT_RULES).lower()
        for forbidden in ("ollama", "qwen", "llm", "gpt", "model"):
            self.assertNotIn(forbidden, names)


def scan_to_verdict(text):
    from phases_oss.reviewers.base import findings_to_verdict

    return findings_to_verdict(scan_text(text, "f.py"))


if __name__ == "__main__":
    unittest.main()
