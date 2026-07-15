"""Group H: integration tests with NO injected fakes.

The central guarantee of the engine -- close re-runs the proof against the
COMMITTED tree -- had zero coverage with the real default_verifier; every
close() test injected a fake. These tests build throwaway git repos and let
default_verifier / default_runner / default_repo_guard do the real work.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from phases_oss import phases
from phases_oss.phases import PhaseError


def _git(cwd, *args):
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), proc.stdout))
    return proc.stdout


def _git_available():
    try:
        return subprocess.run(["git", "--version"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except OSError:
        return False


# The proof imports marker.py from the tree it runs in and checks VALUE.
_PROOF_VALUE_1 = ('"%s" -c "import marker, sys; sys.exit(0 if marker.VALUE == 1 else 1)"'
                  % sys.executable)
_PROOF_VALUE_2 = ('"%s" -c "import marker, sys; sys.exit(0 if marker.VALUE == 2 else 1)"'
                  % sys.executable)


@unittest.skipUnless(_git_available(), "git not available")
class TestRealWorktreeVerify(unittest.TestCase):
    """default_verifier, real git worktree, real subprocess -- no fakes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="phases-real-")
        self.root = Path(self._tmp.name)
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.name", "test")
        _git(self.root, "config", "user.email", "test@example.invalid")
        (self.root / "marker.py").write_text("VALUE = 1\n", encoding="utf-8")
        _git(self.root, "add", "marker.py")
        _git(self.root, "commit", "-q", "-m", "marker VALUE=1")
        self.sha = _git(self.root, "rev-parse", "HEAD").strip()

    def tearDown(self):
        self._tmp.cleanup()

    def test_proof_runs_on_the_commit_not_the_working_copy(self):
        # Working tree diverges: VALUE=2 on disk, VALUE=1 in the commit.
        (self.root / "marker.py").write_text("VALUE = 2\n", encoding="utf-8")
        self.assertTrue(
            phases.default_verifier(self.root, self.sha, _PROOF_VALUE_1),
            "proof true on the commit must verify even if the working copy differs",
        )

    def test_green_working_copy_cannot_stand_in_for_the_commit(self):
        # Proof passes ONLY on the working copy (VALUE=2); the commit has 1.
        (self.root / "marker.py").write_text("VALUE = 2\n", encoding="utf-8")
        self.assertFalse(
            phases.default_verifier(self.root, self.sha, _PROOF_VALUE_2),
            "a green working copy must not pass verification for the commit",
        )

    def test_unknown_commit_fails_closed(self):
        self.assertFalse(
            phases.default_verifier(self.root, "0" * 40, _PROOF_VALUE_1)
        )

    def test_full_close_with_real_verifier_and_guard(self):
        # Complete cycle with NOTHING injected at close: default repo guard
        # and default verifier both run for real.
        phases.init_phase(self.root, objective="real cycle",
                          files_allowed=["marker.py"],
                          proof_command=_PROOF_VALUE_1, level=1)
        phases.approve(self.root)
        code = phases.prove(self.root)  # real default_runner
        self.assertEqual(code, 0)
        report = self.root / "audit.md"
        report.write_text("VERDICT: PASS\n" + "reviewed marker change " * 20,
                          encoding="utf-8")
        phases.record_audit(self.root, reports=[str(report)])
        phases.close(self.root, lesson="real end-to-end", commit_sha=self.sha)
        state = phases.load_state(self.root).data
        self.assertEqual(state["status"], "complete")
        self.assertTrue(state["verify_passed"])


class TestStateCorruptionAndAtomicity(unittest.TestCase):
    def test_corrupted_state_surfaces_phase_error_not_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            phases.init_phase(root, objective="t", files_allowed=["a.py"],
                              proof_command="pytest tests/", level=0)
            state_file = phases.state_paths(root)["state"]
            state_file.write_text('{"active": true, "status": ', encoding="utf-8")
            with self.assertRaises(PhaseError) as ctx:
                phases.load_state(root)
            self.assertIn("reset", str(ctx.exception).lower())
            # every command surfaces the same clean error
            with self.assertRaises(PhaseError):
                phases.prove(root, runner=lambda c, w, env=None: (0, "ok"))
            with self.assertRaises(PhaseError):
                phases.close(root, lesson="x")

    def test_non_object_state_is_a_phase_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = phases.state_paths(root)["dir"]
            state_dir.mkdir(parents=True, exist_ok=True)
            phases.state_paths(root)["state"].write_text("[1, 2]", encoding="utf-8")
            with self.assertRaises(PhaseError):
                phases.load_state(root)

    def test_failed_save_never_truncates_the_previous_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            phases.init_phase(root, objective="v1", files_allowed=["a.py"],
                              proof_command="pytest tests/", level=0)
            state_file = phases.state_paths(root)["state"]
            before = state_file.read_text(encoding="utf-8")
            phase = phases.load_state(root)
            phase.data["objective"] = "v2"
            with mock.patch.object(phases.os, "fsync",
                                   side_effect=OSError("disk gone")):
                with self.assertRaises(OSError):
                    phases.save_state(root, phase)
            after = state_file.read_text(encoding="utf-8")
            self.assertEqual(before, after,
                             "a failed save must leave the previous state intact")
            self.assertEqual(json.loads(after)["objective"], "v1")

    def test_save_leaves_no_temp_file_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            phases.init_phase(root, objective="t", files_allowed=["a.py"],
                              proof_command="pytest tests/", level=0)
            leftovers = list(phases.state_paths(root)["dir"].glob("*.tmp"))
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
