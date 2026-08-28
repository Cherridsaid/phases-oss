"""The router against ten local fixtures, one per project shape.

Each fixture is a few files, not a real application: enough to carry the signal
under test and nothing more. The point is to pin the routing decisions, so a
future edit to the detection rules that silently stops selecting
``shopify-integration-review`` on a Shopify tree fails here instead of in a run.

The suite deliberately asserts *both* directions -- signals that must be
detected and signals that must not be -- because a router that answers SELECTED
to everything would pass a one-directional test while making the matrix
meaningless.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from phases_oss.audit import router
from phases_oss.audit.registry import ORDINALS, by_skill
from phases_oss.audit.runner import AuditRunner
from phases_oss.audit.runstate import TERMINAL

FIXTURES = Path(__file__).parent / "fixtures" / "audit"

# fixture -> (signals that must be present, signals that must be absent)
EXPECTED = {
    "python-lib": ({"tests", "dependencies"}, {"shopify", "mobile", "github_actions"}),
    "javascript-ts": ({"dependencies", "frontend", "api", "tests"}, {"shopify", "iac"}),
    "github-actions": ({"github_actions"}, {"shopify", "mobile"}),
    "iac-terraform": ({"iac", "cloud"}, {"shopify", "mobile"}),
    "shopify-commerce": ({"shopify", "commerce", "webhook"}, {"mobile", "iac"}),
    "saas-multitenant": ({"multitenant", "payment", "auth"}, {"shopify", "mobile"}),
    "ai-rag": ({"ai", "dependencies"}, {"shopify", "mobile"}),
    "mobile-app": ({"mobile"}, {"shopify", "iac"}),
    "clean-project": (set(), {"shopify", "mobile", "iac", "github_actions"}),
    "vulnerable-project": ({"dependencies"}, {"shopify", "mobile"}),
}


class TestFixtureRouting(unittest.TestCase):
    def test_every_fixture_exists(self):
        self.assertTrue(FIXTURES.is_dir(), "fixtures directory is missing")
        present = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())
        self.assertEqual(present, sorted(EXPECTED))

    def test_signals_detected_per_fixture(self):
        for name, (must_have, must_not) in EXPECTED.items():
            with self.subTest(fixture=name):
                signals = router.detect(FIXTURES / name)
                for signal in must_have:
                    self.assertIn(signal, signals, "%s: %s not detected" % (name, signal))
                for signal in must_not:
                    self.assertNotIn(signal, signals, "%s: %s falsely detected" % (name, signal))

    def test_matrix_always_holds_71_entries(self):
        for name in EXPECTED:
            with self.subTest(fixture=name):
                matrix = router.build_matrix(FIXTURES / name)
                entries = [k for k in matrix if k != "_signals"]
                self.assertEqual(len(entries), 71)
                self.assertEqual(sorted(entries), ["%02d" % i for i in range(1, 72)])

    def test_shopify_fixture_selects_the_shopify_phase(self):
        matrix = router.build_matrix(FIXTURES / "shopify-commerce")
        ordinal = by_skill("shopify-integration-review").ordinal
        self.assertEqual(matrix["%02d" % ordinal]["decision"], router.SELECTED)

    def test_clean_fixture_skips_the_product_phases(self):
        matrix = router.build_matrix(FIXTURES / "clean-project")
        for skill in ("shopify-integration-review", "mobile-security-review",
                      "app-store-compliance", "iac-security", "gha-security-review"):
            ordinal = by_skill(skill).ordinal
            with self.subTest(skill=skill):
                self.assertEqual(matrix["%02d" % ordinal]["decision"], router.NOT_APPLICABLE)

    def test_evidence_names_the_file_that_triggered_the_signal(self):
        signals = router.detect(FIXTURES / "shopify-commerce")
        self.assertIn("shopify", signals.evidence)
        self.assertTrue(signals.evidence["shopify"])


class TestFixtureRun(unittest.TestCase):
    """A full 71-phase run over the smallest fixture, end to end."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="phases-fixture-run-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_clean_fixture_visits_all_71_phases_without_touching_it(self):
        target = self.tmp / "clean-project"
        shutil.copytree(FIXTURES / "clean-project", target)
        roots = self.tmp / "skills"
        for spec in ORDINALS:
            (roots / spec.skill).mkdir(parents=True, exist_ok=True)
            (roots / spec.skill / "SKILL.md").write_text("# %s\n" % spec.skill, encoding="utf-8")

        state = AuditRunner(
            target,
            root=self.tmp / "runroot",
            skill_roots=[roots],
            stage_parent=self.tmp / "stages",
        ).run()

        self.assertEqual(len(state.phases), 71)
        self.assertTrue(state.is_complete())
        self.assertTrue(all(e["status"] in TERMINAL for e in state.phases))
        self.assertEqual(sum(state.summary().values()), 71)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
