"""Tests for the opt-in cloud reviewer.

The key safety properties under test:
* no sender  -> inert, no network, and REVIEW_UNAVAILABLE (fail closed);
* sender + disallowed destination -> sender NEVER called, REVIEW_UNAVAILABLE;
* sender + allowed destination -> sender sees REDACTED text only;
* unreachable / empty / garbled responses -> REVIEW_UNAVAILABLE, never PASS;
* the module imports no networking library.
"""

import tempfile
import unittest
from pathlib import Path

from phases_oss.data_gate import DataGate
from phases_oss.phases import Phase, ReviewVerdict
from phases_oss.reviewers.cloud import CloudReviewer, default_build_prompt, parse_response


class SpySender:
    """Records every call so tests can assert what (if anything) was sent."""

    def __init__(self, reply="VERDICT: PASS ok"):
        self.calls = []
        self.reply = reply

    def __call__(self, payload, destination):
        self.calls.append((payload, destination))
        return self.reply


def make_phase(root, body='token = "AKIA1234567890ABCDEF"\n'):
    f = Path(root) / "change.py"
    f.write_text(body, encoding="utf-8")
    return Phase(
        {
            "objective": "do the thing",
            "files_allowed": [str(f)],
        }
    )


class TestInert(unittest.TestCase):
    def test_no_sender_is_unavailable_not_pass(self):
        reviewer = CloudReviewer()  # no sender, no endpoint
        self.assertFalse(reviewer.is_configured())
        with tempfile.TemporaryDirectory() as tmp:
            verdict = reviewer.review(make_phase(tmp))
        # Fail closed: an absent backend can never count as an approval.
        self.assertEqual(verdict.verdict, ReviewVerdict.UNAVAILABLE)
        self.assertFalse(verdict.passed)
        self.assertIn("not configured", verdict.notes)

    def test_sender_without_endpoint_is_inert(self):
        spy = SpySender()
        reviewer = CloudReviewer(sender=spy, endpoint="")
        self.assertFalse(reviewer.is_configured())
        with tempfile.TemporaryDirectory() as tmp:
            reviewer.review(make_phase(tmp))
        self.assertEqual(spy.calls, [])


class TestGateEnforced(unittest.TestCase):
    def test_disallowed_destination_never_calls_sender(self):
        spy = SpySender()
        # default gate has an empty allowlist => deny all
        reviewer = CloudReviewer(sender=spy, endpoint="https://api.example.com/v1")
        with tempfile.TemporaryDirectory() as tmp:
            verdict = reviewer.review(make_phase(tmp))
        self.assertEqual(spy.calls, [], "sender was called for a disallowed destination")
        # The review did not happen -> unavailable, never a silent pass.
        self.assertEqual(verdict.verdict, ReviewVerdict.UNAVAILABLE)
        self.assertIn("data gate", verdict.notes)

    def test_allowed_destination_sends_redacted_text_only(self):
        spy = SpySender()
        gate = DataGate(allowlist=["api.example.com"])
        reviewer = CloudReviewer(sender=spy, gate=gate, endpoint="https://api.example.com/v1")
        with tempfile.TemporaryDirectory() as tmp:
            reviewer.review(make_phase(tmp))
        self.assertEqual(len(spy.calls), 1)
        sent_payload, sent_dest = spy.calls[0]
        self.assertNotIn("AKIA1234567890ABCDEF", sent_payload, "raw secret reached the sender")
        # whichever rule fired first, the value must be replaced by a placeholder
        self.assertIn("[REDACTED:", sent_payload)
        self.assertEqual(sent_dest, "https://api.example.com/v1")

    def test_disclosure_attached_to_notes(self):
        spy = SpySender()
        gate = DataGate(allowlist=["api.example.com"])
        reviewer = CloudReviewer(sender=spy, gate=gate, endpoint="https://api.example.com/v1")
        with tempfile.TemporaryDirectory() as tmp:
            verdict = reviewer.review(make_phase(tmp))
        self.assertIn("Data gate disclosure", verdict.notes)


class TestResponseHandling(unittest.TestCase):
    def test_refus_response_blocks(self):
        spy = SpySender(reply="VERDICT: REFUS line 3 is wrong")
        gate = DataGate(allowlist=["api.example.com"])
        reviewer = CloudReviewer(sender=spy, gate=gate, endpoint="https://api.example.com/v1")
        with tempfile.TemporaryDirectory() as tmp:
            verdict = reviewer.review(make_phase(tmp))
        self.assertEqual(verdict.verdict, ReviewVerdict.REFUS)
        self.assertFalse(verdict.passed)

    def test_unreachable_sender_is_unavailable(self):
        def boom(_p, _d):
            raise OSError("connection refused")

        gate = DataGate(allowlist=["api.example.com"])
        reviewer = CloudReviewer(sender=boom, gate=gate, endpoint="https://api.example.com/v1")
        with tempfile.TemporaryDirectory() as tmp:
            verdict = reviewer.review(make_phase(tmp))
        self.assertEqual(verdict.verdict, ReviewVerdict.UNAVAILABLE)
        self.assertFalse(verdict.passed)
        self.assertIn("unreachable", verdict.notes)


class TestParseResponse(unittest.TestCase):
    def test_refus(self):
        self.assertEqual(parse_response("VERDICT: REFUS").verdict, ReviewVerdict.REFUS)

    def test_exploited(self):
        self.assertEqual(parse_response("found EXPLOITED bug").verdict, ReviewVerdict.REFUS)

    def test_pass(self):
        self.assertEqual(parse_response("VERDICT: PASS good").verdict, ReviewVerdict.PASS_WITH_NOTES)

    def test_empty_is_unavailable(self):
        self.assertEqual(parse_response("").verdict, ReviewVerdict.UNAVAILABLE)
        self.assertEqual(parse_response("   ").verdict, ReviewVerdict.UNAVAILABLE)
        self.assertFalse(parse_response("").passed)

    def test_garbage_is_unavailable(self):
        v = parse_response("weather is nice today")
        self.assertEqual(v.verdict, ReviewVerdict.UNAVAILABLE)
        self.assertFalse(v.passed)
        self.assertIn("unrecognized", v.notes)

    def test_stray_pass_word_does_not_approve(self):
        # "pass" inside prose is not a verdict; only the explicit marker is.
        for prose in (
            "I cannot pass judgment on this diff",
            "the tests PASS but I was not able to review",
            "please let this pass",
        ):
            with self.subTest(prose=prose):
                v = parse_response(prose)
                self.assertEqual(v.verdict, ReviewVerdict.UNAVAILABLE)
                self.assertFalse(v.passed)

    def test_explicit_marker_still_passes(self):
        v = parse_response("VERDICT: PASS\nlooks correct")
        self.assertEqual(v.verdict, ReviewVerdict.PASS_WITH_NOTES)
        self.assertTrue(v.passed)


class TestNoNetworkImport(unittest.TestCase):
    # Forbidden module prefixes (any networking-capable stdlib or third party).
    FORBIDDEN = (
        "socket", "socketserver", "ssl", "http", "urllib.request", "ftplib",
        "smtplib", "poplib", "imaplib", "telnetlib", "xmlrpc", "asyncio",
        "requests", "httpx", "aiohttp", "urllib3",
    )

    def _imported_names(self, path):
        import ast

        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if base:
                    names.add(base)
                for alias in node.names:
                    names.add((base + "." + alias.name) if base else alias.name)
        return names

    def test_module_imports_no_network_lib(self):
        # AST-based (not a substring grep): catches `from urllib import request`,
        # aliased imports, and function-local imports alike.
        import phases_oss.reviewers.cloud as mod

        for name in self._imported_names(mod.__file__):
            for bad in self.FORBIDDEN:
                self.assertFalse(
                    name == bad or name.startswith(bad + "."),
                    "cloud reviewer must not import a network module: %s" % name,
                )


class TestRootResolution(unittest.TestCase):
    """Relative files_allowed must resolve against the repo root, not the cwd."""

    def test_build_prompt_resolves_relative_files_against_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "mod.py").write_text("MARKER_CONTENT = 1\n", encoding="utf-8")
            phase = Phase({"objective": "x", "files_allowed": ["mod.py"]})
            # cwd is the test runner's directory, where mod.py does not exist.
            prompt = default_build_prompt(phase, root=Path(tmp))
        self.assertIn("MARKER_CONTENT", prompt)

    def test_build_prompt_without_root_misses_relative_file(self):
        # Documents the failure mode the root parameter exists to fix.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "mod.py").write_text("MARKER_CONTENT = 1\n", encoding="utf-8")
            phase = Phase({"objective": "x", "files_allowed": ["mod.py"]})
            prompt = default_build_prompt(phase)
        self.assertNotIn("MARKER_CONTENT", prompt)

    def test_reviewer_root_reaches_the_sender_payload(self):
        spy = SpySender()
        gate = DataGate(allowlist=["api.example.com"])
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "mod.py").write_text("MARKER_CONTENT = 1\n", encoding="utf-8")
            reviewer = CloudReviewer(
                sender=spy, gate=gate, endpoint="https://api.example.com/v1", root=tmp
            )
            reviewer.review(Phase({"objective": "x", "files_allowed": ["mod.py"]}))
        self.assertEqual(len(spy.calls), 1)
        self.assertIn("MARKER_CONTENT", spy.calls[0][0])

    def test_absolute_files_ignore_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "abs.py"
            f.write_text("ABS_MARKER = 1\n", encoding="utf-8")
            phase = Phase({"objective": "x", "files_allowed": [str(f)]})
            prompt = default_build_prompt(phase, root=Path(tmp) / "elsewhere")
        self.assertIn("ABS_MARKER", prompt)


class TestRegistry(unittest.TestCase):
    def test_get_reviewer_cloud(self):
        from phases_oss.reviewers import get_reviewer

        self.assertIsInstance(get_reviewer("cloud"), CloudReviewer)


if __name__ == "__main__":
    unittest.main()
