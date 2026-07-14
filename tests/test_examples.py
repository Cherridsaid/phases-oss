"""Prove the runnable example actually runs and behaves as documented."""

import io
import os
import sys
import unittest
from unittest import mock

EXAMPLES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "examples"))
if EXAMPLES_DIR not in sys.path:
    sys.path.insert(0, EXAMPLES_DIR)

import review_demo  # noqa: E402  (path set above)


class TestReviewDemo(unittest.TestCase):
    def test_runs_and_returns_zero(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = review_demo.main()
        self.assertEqual(rc, 0)
        text = out.getvalue()
        # the static reviewer found the planted issues
        self.assertIn("hardcoded-secret", text)
        self.assertIn("debugger", text)
        self.assertIn("verdict: REFUS", text)
        # the cloud reviewer stayed inert
        self.assertIn("is_configured: False", text)
        self.assertIn("not configured", text)

    def test_demo_imports_no_network_lib(self):
        import ast

        with open(review_demo.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(node.module or "")
        for bad in ("socket", "urllib", "http", "requests", "httpx"):
            self.assertNotIn(bad, names)


if __name__ == "__main__":
    unittest.main()
