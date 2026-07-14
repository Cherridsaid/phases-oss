"""Unit tests for the phase state machine.

Side effects (running the proof, verifying the commit) are injected, so these
tests never spawn a real subprocess or git worktree.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from phases_oss import phases
from phases_oss.phases import PhaseError, ReviewVerdict


def _git(cwd, *args):
    """Run git in cwd, returning stdout; raises if git fails (test-only helper)."""
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), proc.stdout))
    return proc.stdout


def _git_available():
    try:
        return subprocess.run(
            ["git", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
    except OSError:
        return False


def ok_runner(_command, _cwd):
    return 0, "all good\n"


def fail_runner(_command, _cwd):
    return 1, "boom\n"


def pass_verifier(_root, _sha, _cmd):
    return True


def fail_verifier(_root, _sha, _cmd):
    return False


def noop_repo_guard(_root, _files, _sha):
    """No-op repo guard for unit tests that use a fake commit_sha.

    The real default_repo_guard needs a git repo; injecting this keeps the
    existing close() tests free of git while the guard itself is exercised by
    TestRepoMatchGuard against real throwaway repos.
    """
    return None


class PhaseTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="phases-test-")
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _init(self, **over):
        kw = dict(
            objective="do a thing",
            files_allowed=["a.py"],
            proof_command="python run_tests.py",
            audit="none",
        )
        kw.update(over)
        return phases.init_phase(self.root, **kw)

    def _report(self, name, body="VERDICT: PASS\n" + "x" * 300):
        p = self.root / name
        p.write_text(body, encoding="utf-8")
        return str(p)


class TestInit(PhaseTestBase):
    def test_rejects_trivial_proof(self):
        for bad in ("exit 0", "true", ":", "  EXIT 0 "):
            with self.subTest(bad=bad):
                with self.assertRaises(PhaseError):
                    self._init(proof_command=bad)

    def test_rejects_empty_objective(self):
        with self.assertRaises(PhaseError):
            self._init(objective="   ")

    def test_rejects_empty_files(self):
        with self.assertRaises(PhaseError):
            self._init(files_allowed=["  ", ""])

    def test_rejects_unknown_audit(self):
        with self.assertRaises(PhaseError):
            self._init(audit="paranoid")

    def test_creates_pending_state(self):
        self._init()
        phase = phases.load_state(self.root)
        self.assertIsNotNone(phase)
        self.assertTrue(phase.data["active"])
        self.assertEqual(phase.data["status"], "pending_approval")
        self.assertEqual(phase.data["files_allowed"], ["a.py"])
        # audit "none" means the audit gate is already satisfied at init.
        self.assertTrue(phase.data["audit_passed"])

    def test_state_lives_under_dot_claude(self):
        self._init()
        self.assertTrue((self.root / ".claude" / "phase-state.json").exists())


class TestApproveProve(PhaseTestBase):
    def test_approve_requires_pending(self):
        with self.assertRaises(PhaseError):
            phases.approve(self.root)  # no phase
        self._init()
        phases.approve(self.root)
        self.assertEqual(phases.load_state(self.root).data["status"], "active")
        with self.assertRaises(PhaseError):
            phases.approve(self.root)  # already active

    def test_prove_requires_active(self):
        self._init()
        with self.assertRaises(PhaseError):
            phases.prove(self.root, runner=ok_runner)  # not approved

    def test_prove_pass_resets_attempts(self):
        self._init()
        phases.approve(self.root)
        code = phases.prove(self.root, runner=ok_runner)
        self.assertEqual(code, 0)
        phase = phases.load_state(self.root)
        self.assertTrue(phase.data["proof_passed"])
        self.assertEqual(phase.data["attempts"], 0)
        self.assertIn("all good", phase.data["proof_output"])

    def test_prove_fail_increments_attempts(self):
        self._init()
        phases.approve(self.root)
        phases.prove(self.root, runner=fail_runner)
        phases.prove(self.root, runner=fail_runner)
        phase = phases.load_state(self.root)
        self.assertFalse(phase.data["proof_passed"])
        self.assertEqual(phase.data["attempts"], 2)


class TestAudit(PhaseTestBase):
    def _ready(self, audit):
        self._init(audit=audit)
        phases.approve(self.root)
        phases.prove(self.root, runner=ok_runner)

    def test_none_auto_passes(self):
        self._ready("none")
        phase = phases.record_audit(self.root)
        self.assertTrue(phase.data["audit_passed"])
        self.assertEqual(phase.data["audit_agent_count"], 0)

    def test_review_needs_one_report(self):
        self._ready("review")
        with self.assertRaises(PhaseError):
            phases.record_audit(self.root, reports=[])
        phases.record_audit(self.root, reports=[self._report("r1.md")])
        self.assertTrue(phases.load_state(self.root).data["audit_passed"])

    def test_level2_requires_full_suite_and_one_review(self):
        # Normalized policy: levels 1-3 need exactly ONE independent review;
        # level >= 2 additionally requires the full-suite declaration at init.
        with self.assertRaises(PhaseError):
            self._init(audit="security")  # legacy alias for level 2, no full_suite
        self._init(level=2, full_suite=True)
        phases.approve(self.root)
        phases.prove(self.root, runner=ok_runner)
        with self.assertRaises(PhaseError):
            phases.record_audit(self.root, reports=[])
        phases.record_audit(self.root, reports=[self._report("r1.md")])
        state = phases.load_state(self.root).data
        self.assertTrue(state["audit_passed"])
        self.assertEqual(state["audit_agent_count"], 1)
        self.assertEqual(state["risk_level"], 2)
        self.assertEqual(state["review_mode"], "strict")
        self.assertEqual(state["proof_scope"], "full")

    def test_report_too_short_rejected(self):
        self._ready("review")
        with self.assertRaises(PhaseError):
            phases.record_audit(self.root, reports=[self._report("s.md", "VERDICT: tiny")])

    def test_report_without_verdict_rejected(self):
        self._ready("review")
        with self.assertRaises(PhaseError):
            phases.record_audit(self.root, reports=[self._report("n.md", "no keyword " + "z" * 300)])

    def test_exploited_reopens(self):
        self._init(level=2, full_suite=True)
        phases.approve(self.root)
        phases.prove(self.root, runner=ok_runner)
        reports = [
            self._report("a.md", "VERDICT: EXPLOITED " + "a" * 300),
        ]
        with self.assertRaises(PhaseError):
            phases.record_audit(self.root, reports=reports, open_exploited=2)
        phase = phases.load_state(self.root)
        self.assertFalse(phase.data["audit_passed"])
        self.assertEqual(phase.data["open_exploited"], 2)
        self.assertEqual(phase.data["status"], "active")


class TestReview(PhaseTestBase):
    def test_records_verdict(self):
        self._init(audit="review")
        phases.approve(self.root)
        verdict = phases.review(self.root, lambda ph: ReviewVerdict(ReviewVerdict.PASS, "ok"))
        self.assertTrue(verdict.passed)
        stored = phases.load_state(self.root).data["review_verdict"]
        self.assertEqual(stored["verdict"], "PASS")

    def test_refus_persisted_not_raised(self):
        self._init(audit="review")
        phases.approve(self.root)
        verdict = phases.review(
            self.root, lambda ph: ReviewVerdict(ReviewVerdict.REFUS, "bad", "fix line 3")
        )
        self.assertFalse(verdict.passed)
        self.assertEqual(phases.load_state(self.root).data["review_verdict"]["action"], "fix line 3")


class TestClose(PhaseTestBase):
    def _proved(self, audit="none"):
        self._init(audit=audit)
        phases.approve(self.root)
        phases.prove(self.root, runner=ok_runner)

    def test_close_requires_proof(self):
        self._init()
        phases.approve(self.root)
        with self.assertRaises(PhaseError):
            phases.close(self.root, lesson="x", verifier=pass_verifier)

    def test_close_requires_lesson(self):
        self._proved()
        phases.record_audit(self.root)
        with self.assertRaises(PhaseError):
            phases.close(self.root, lesson="   ", verifier=pass_verifier)

    def test_close_requires_audit(self):
        self._proved(audit="review")
        # audit not recorded -> audit_passed False
        with self.assertRaises(PhaseError):
            phases.close(self.root, lesson="learned", verifier=pass_verifier)

    def test_close_verify_on_commit_fails(self):
        self._proved()
        phases.record_audit(self.root)
        with self.assertRaises(PhaseError):
            phases.close(
                self.root,
                lesson="learned",
                commit_sha="deadbeef",
                verifier=fail_verifier,
                repo_guard=noop_repo_guard,
            )

    def test_close_happy_path_appends_log(self):
        self._proved()
        phases.record_audit(self.root)
        phase = phases.close(
            self.root,
            lesson="a lesson",
            commit_sha="cafe",
            verifier=pass_verifier,
            repo_guard=noop_repo_guard,
        )
        self.assertEqual(phase.data["status"], "complete")
        self.assertFalse(phase.data["active"])
        self.assertTrue(phase.data["verify_passed"])
        # The journal is now the v2 event stream; the last line is the
        # phase_closed event whose payload snapshots the whole state.
        log = (self.root / ".claude" / "phase-log.jsonl").read_text(encoding="utf-8").strip()
        events = [json.loads(line) for line in log.splitlines()]
        closed = events[-1]
        self.assertEqual(closed["event_type"], "phase_closed")
        self.assertEqual(closed["schema_version"], 2)
        self.assertEqual(closed["payload"]["state"]["lesson"], "a lesson")
        self.assertEqual(closed["payload"]["state"]["commit_sha"], "cafe")

    def test_level3_requires_runtime_and_human_validation(self):
        self._init(level=3, full_suite=True)
        phases.approve(self.root)
        phases.prove(self.root, runner=ok_runner)
        phases.record_audit(
            self.root, reports=[self._report("a.md", "VERDICT: PASS " + "a" * 300)]
        )
        with self.assertRaises(PhaseError):  # runtime not proven
            phases.close(
                self.root, lesson="x", commit_sha="cafe",
                verifier=pass_verifier, repo_guard=noop_repo_guard,
            )
        phases.runtime(self.root, self._report("rt.md", "VERDICT: isolated " + "r" * 300))
        with self.assertRaises(PhaseError):  # human validation still missing
            phases.close(
                self.root, lesson="x", commit_sha="cafe",
                verifier=pass_verifier, repo_guard=noop_repo_guard,
            )
        phases.human_approve(self.root, validator="said")
        phases.close(
            self.root, lesson="x", commit_sha="cafe",
            verifier=pass_verifier, repo_guard=noop_repo_guard,
        )
        state = phases.load_state(self.root).data
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["human_validator"], "said")

    def test_human_approve_reserved_for_level3(self):
        self._init(level=1)
        phases.approve(self.root)
        with self.assertRaises(PhaseError):
            phases.human_approve(self.root, validator="said")


class TestRepoMatchGuard(unittest.TestCase):
    """P28b: close must refuse a commit_sha that does not belong to the repo
    holding the files_allowed. Exercises the real default_repo_guard against
    throwaway git repos (skipped if git is unavailable)."""

    def setUp(self):
        if not _git_available():
            self.skipTest("git not available")
        self._tmp = tempfile.TemporaryDirectory(prefix="phases-rm-")
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_repo(self, name, filename="f.py", content="x = 1\n"):
        repo = self.base / name
        repo.mkdir(parents=True)
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.email", "t@local")
        _git(repo, "config", "user.name", "t")
        (repo / filename).write_text(content, encoding="utf-8")
        _git(repo, "add", filename)
        _git(repo, "commit", "-m", "seed")
        sha = _git(repo, "rev-parse", "HEAD").strip()
        return repo, sha

    def test_rejects_commit_from_unrelated_repo(self):
        repo_a, _ = self._make_repo("A", filename="a.py")
        _repo_b, sha_b = self._make_repo("B", filename="b.py")
        with self.assertRaises(PhaseError) as cm:
            phases.default_repo_guard(repo_a, ["a.py"], sha_b)
        self.assertIn("absent", str(cm.exception))

    def test_rejects_file_outside_any_git_repo(self):
        plain = self.base / "plain"
        plain.mkdir()
        (plain / "x.py").write_text("y = 2\n", encoding="utf-8")
        _repo, sha = self._make_repo("R")
        with self.assertRaises(PhaseError) as cm:
            phases.default_repo_guard(plain, ["x.py"], sha)
        self.assertIn("no git repo", str(cm.exception))

    def test_rejects_multi_repo_files_allowed(self):
        repo_a, sha_a = self._make_repo("A", filename="a.py")
        repo_b, _ = self._make_repo("B", filename="b.py")
        with self.assertRaises(PhaseError) as cm:
            phases.default_repo_guard(
                repo_a, ["a.py", str(repo_b / "b.py")], sha_a
            )
        self.assertIn("span", str(cm.exception))

    def test_rejects_commit_touching_no_allowed_file(self):
        repo, _seed = self._make_repo("R", filename="a.py")
        # a second commit that touches only b.py, not the allowed a.py
        (repo / "b.py").write_text("z = 3\n", encoding="utf-8")
        _git(repo, "add", "b.py")
        _git(repo, "commit", "-m", "other")
        sha_other = _git(repo, "rev-parse", "HEAD").strip()
        with self.assertRaises(PhaseError) as cm:
            phases.default_repo_guard(repo, ["a.py"], sha_other)
        self.assertIn("touches no file", str(cm.exception))

    def test_case_sensitive_filename_on_case_sensitive_fs(self):
        # Codex-found bug: lowercasing filenames let a commit touching 'A.py'
        # satisfy an allowed 'a.py'. On a case-sensitive FS they must differ.
        if os.path.normcase("A.py") == os.path.normcase("a.py"):
            self.skipTest("case-insensitive filesystem")
        repo, _seed = self._make_repo("R", filename="a.py")
        (repo / "A.py").write_text("upper = 1\n", encoding="utf-8")
        _git(repo, "add", "A.py")
        _git(repo, "commit", "-m", "upper only")
        sha_upper = _git(repo, "rev-parse", "HEAD").strip()
        with self.assertRaises(PhaseError) as cm:
            phases.default_repo_guard(repo, ["a.py"], sha_upper)
        self.assertIn("touches no file", str(cm.exception))

    def test_rejects_empty_files_allowed(self):
        _repo, sha = self._make_repo("R")
        with self.assertRaises(PhaseError):
            phases.default_repo_guard(self.base, [], sha)

    def test_accepts_matching_commit(self):
        repo, sha = self._make_repo("R", filename="a.py")
        # must not raise: sha touches a.py in repo R
        phases.default_repo_guard(repo, ["a.py"], sha)

    def test_accepts_repo_root_scope(self):
        # files_allowed = ['.'] means the whole repo; any touched file counts.
        repo, sha = self._make_repo("R", filename="a.py")
        phases.default_repo_guard(repo, ["."], sha)

    def test_accepts_subdir_scope(self):
        repo, _seed = self._make_repo("R", filename="keep.py")
        (repo / "src").mkdir()
        (repo / "src" / "a.py").write_text("v = 1\n", encoding="utf-8")
        _git(repo, "add", "src/a.py")
        _git(repo, "commit", "-m", "add src")
        sha = _git(repo, "rev-parse", "HEAD").strip()
        # a directory in files_allowed matches a commit touching a file beneath it
        phases.default_repo_guard(repo, ["src"], sha)

    def test_accepts_commit_seen_from_verify_worktree(self):
        # A throwaway worktree of the repo is still the same repo (git-common-dir).
        repo, sha = self._make_repo("R", filename="a.py")
        wt = self.base / "wt"
        _git(repo, "worktree", "add", "--detach", str(wt), sha)
        try:
            phases.default_repo_guard(wt, ["a.py"], sha)
        finally:
            _git(repo, "worktree", "remove", "--force", str(wt))


class TestAnalysisGate(PhaseTestBase):
    """Pre-phase analysis gate: strict metadata at init, enforced at close,
    never copied into the journal (only the artifact reference is kept)."""

    _META = dict(
        require_analysis=True,
        analysis_context="code_audit",
        analysis_depth="core",
        analysis_axes=["surface", "risques"],
        analysis_ref="artifact://analysis/md_0123456789abcdef01234567",
    )

    def test_missing_context_refused(self):
        kw = dict(self._META, analysis_context="  ")
        with self.assertRaises(PhaseError) as cm:
            self._init(**kw)
        self.assertIn("ANALYSIS_CONTEXT_MISSING", str(cm.exception))

    def test_depth_must_match_level(self):
        # Level 0-1 expect 'core'; level 2 expects 'deep'; level 3 'full'.
        kw = dict(self._META, analysis_depth="deep")
        with self.assertRaises(PhaseError) as cm:
            self._init(**kw)  # level 0 -> expects core
        self.assertIn("ANALYSIS_DEPTH_MISMATCH", str(cm.exception))
        self._init(level=2, full_suite=True, **dict(self._META, analysis_depth="deep"))
        self.assertEqual(phases.load_state(self.root).data["analysis"]["depth"], "deep")

    def test_missing_axes_refused(self):
        kw = dict(self._META, analysis_axes=[])
        with self.assertRaises(PhaseError) as cm:
            self._init(**kw)
        self.assertIn("ANALYSIS_AXES_MISSING", str(cm.exception))

    def test_invalid_ref_refused(self):
        for bad in ("", "md_123", "http://x/y", "artifact://", "artifact://UPPER/id"):
            with self.subTest(bad=bad):
                kw = dict(self._META, analysis_ref=bad)
                with self.assertRaises(PhaseError) as cm:
                    self._init(**kw)
                self.assertIn("ANALYSIS_REF_INVALID", str(cm.exception))

    def test_close_refuses_without_analysis_metadata(self):
        self._init(**self._META)
        phases.approve(self.root)
        phases.prove(self.root, runner=ok_runner)
        phases.record_audit(self.root)
        # Simulate a state where the requirement survived but the metadata
        # was lost (e.g. hand-edited state): close must fail closed.
        phase = phases.load_state(self.root)
        phase.data["analysis"] = None
        phases.save_state(self.root, phase)
        with self.assertRaises(PhaseError) as cm:
            phases.close(self.root, lesson="x", verifier=pass_verifier)
        self.assertIn("ANALYSIS_REQUIRED", str(cm.exception))

    def test_journal_keeps_metadata_only(self):
        self._init(**self._META)
        log = (self.root / ".claude" / "phase-log.jsonl").read_text(encoding="utf-8")
        events = [json.loads(line) for line in log.strip().splitlines()]
        completed = [e for e in events if e["event_type"] == "analysis.completed"]
        self.assertEqual(len(completed), 1)
        evt = completed[0]
        # Metadata present, payload empty: the analysis text itself is never
        # copied into the journal, only the artifact pointer.
        self.assertEqual(evt["analysis"]["analysis_ref"], self._META["analysis_ref"])
        self.assertEqual(evt["analysis"]["axes"], ["surface", "risques"])
        self.assertEqual(evt["payload"], {})


class TestJournalV2(PhaseTestBase):
    def _events(self):
        log = (self.root / ".claude" / "phase-log.jsonl").read_text(encoding="utf-8")
        return [json.loads(line) for line in log.strip().splitlines()]

    def test_every_transition_appends_an_event(self):
        self._init(audit="review")
        phases.approve(self.root)
        phases.prove(self.root, runner=ok_runner)
        phases.record_audit(self.root, reports=[self._report("r.md")])
        phases.review(self.root, lambda ph: ReviewVerdict(ReviewVerdict.PASS, "ok"))
        phases.close(
            self.root, lesson="l", commit_sha="cafe",
            verifier=pass_verifier, repo_guard=noop_repo_guard,
        )
        types = [e["event_type"] for e in self._events()]
        self.assertEqual(
            types,
            [
                "phase_initialized",
                "phase_approved",
                "proof_completed",
                "audit_recorded",
                "review_recorded",
                "phase_closed",
            ],
        )

    def test_events_share_ids_and_carry_required_fields(self):
        self._init()
        phases.approve(self.root)
        events = self._events()
        required = {
            "schema_version", "event_id", "phase_id", "session_id",
            "project_id", "review_id", "finding_id", "timestamp_utc",
            "event_type", "payload",
        }
        for evt in events:
            self.assertTrue(required.issubset(evt.keys()))
            self.assertEqual(evt["schema_version"], 2)
        self.assertEqual(len({e["phase_id"] for e in events}), 1)
        self.assertEqual(len({e["project_id"] for e in events}), 1)
        self.assertEqual(len({e["event_id"] for e in events}), len(events))
        # project id persisted for the next phases of the same project
        self.assertTrue((self.root / ".claude" / "project-id").exists())

    def test_review_event_carries_review_id(self):
        self._init(audit="review")
        phases.approve(self.root)
        phases.review(self.root, lambda ph: ReviewVerdict(ReviewVerdict.REFUS, "bad", "fix"))
        reviews = [e for e in self._events() if e["event_type"] == "review_recorded"]
        self.assertEqual(len(reviews), 1)
        self.assertTrue(reviews[0]["review_id"].startswith("rev_"))
        self.assertEqual(reviews[0]["payload"]["verdict"], "REFUS")
        stored = phases.load_state(self.root).data["review_verdict"]
        self.assertEqual(stored["review_id"], reviews[0]["review_id"])

    def test_state_snapshot_allows_reconstruction(self):
        self._init()
        phases.approve(self.root)
        phases.prove(self.root, runner=ok_runner)
        last = self._events()[-1]
        snapshot = last["payload"]["state"]
        live = phases.load_state(self.root).data
        self.assertEqual(snapshot["status"], live["status"])
        self.assertEqual(snapshot["proof_passed"], live["proof_passed"])

    def test_reset_journals_a_terminal_event(self):
        self._init()
        phases.reset(self.root)
        events = self._events()
        self.assertEqual(events[-1]["event_type"], "phase_reset")
        self.assertFalse(events[-1]["payload"]["state"]["active"])


class TestLegacyStateMigration(PhaseTestBase):
    """A pre-v4 state (no risk_level) must keep its audit requirement.

    Falling back to level 0 would silently drop the audit gate at close --
    exactly the fail-open class the repo-match work banned. The level is
    derived from the legacy audit_required name; an unknown name maps to
    the strictest level, never the weakest.
    """

    def _write_legacy_state(self, audit_required):
        data = {
            "version": 3,
            "active": True,
            "status": "active",
            "objective": "legacy phase",
            "files_allowed": ["a.py"],
            "proof_command": "python run_tests.py",
            "proof_passed": True,
            "audit_required": audit_required,
            "audit_passed": False,
            "audit_agent_count": 0,
            "open_exploited": 0,
            "runtime_passed": False,
            "runtime_report": "",
            # deliberately NO risk_level / full_suite_declared / human_* fields
        }
        phases.save_state(self.root, phases.Phase(data))

    def test_legacy_security_state_still_requires_a_report(self):
        self._write_legacy_state("security")
        with self.assertRaises(PhaseError):
            phases.record_audit(self.root, reports=[])  # need derived: 1, not 0
        phases.record_audit(self.root, reports=[self._report("r.md")])
        self.assertTrue(phases.load_state(self.root).data["audit_passed"])

    def test_legacy_state_close_keeps_the_audit_gate(self):
        self._write_legacy_state("review")
        # audit never recorded -> close must refuse, not sail through at level 0
        with self.assertRaises(PhaseError):
            phases.close(self.root, lesson="x", verifier=pass_verifier)

    def test_unknown_audit_name_maps_to_strictest_level(self):
        self.assertEqual(phases._phase_level({"audit_required": "paranoid"}), 3)
        self.assertEqual(phases._phase_level({}), 0)  # explicit legacy "none" default
        self.assertEqual(phases._phase_level({"audit_required": "none"}), 0)
        self.assertEqual(phases._phase_level({"risk_level": 2}), 2)


class TestReset(PhaseTestBase):
    def test_reset_removes_state(self):
        self._init()
        self.assertIsNotNone(phases.load_state(self.root))
        phases.reset(self.root)
        self.assertIsNone(phases.load_state(self.root))


class TestVerifyEnv(unittest.TestCase):
    """The proof re-run env must not let the working tree pass for the commit."""

    def _with_env(self, overrides):
        saved = {k: os.environ.get(k) for k in overrides}

        def restore():
            for k, old in saved.items():
                if old is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = old

        self.addCleanup(restore)
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_strips_interpreter_knobs_and_prepends_src(self):
        self._with_env({
            "PYTHONPATH": "/some/editable/src",
            "VIRTUAL_ENV": "/some/venv",
            "PYTHONHOME": "/some/home",
        })
        with tempfile.TemporaryDirectory() as tmp:
            worktree = os.path.join(tmp, "wt")
            os.makedirs(os.path.join(worktree, "src"))
            env = phases._verify_env(phases.Path(worktree))
        self.assertNotIn("VIRTUAL_ENV", env)
        self.assertNotIn("PYTHONHOME", env)
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")
        self.assertEqual(env["PYTHONPATH"], os.path.join(worktree, "src"))
        self.assertNotIn("/some/editable/src", env["PYTHONPATH"])

    def test_pythonpath_falls_back_to_worktree_when_no_src(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = os.path.join(tmp, "wt")
            os.makedirs(worktree)
            env = phases._verify_env(phases.Path(worktree))
        self.assertEqual(env["PYTHONPATH"], worktree)


if __name__ == "__main__":
    unittest.main()
