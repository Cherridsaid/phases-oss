"""Tests for the project-local hook installer.

All tests operate on throwaway temp directories; the installer is never run
against a real project or the global config.
"""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from phases_oss import install
from phases_oss.install import InstallError


class InstallTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()
        self.project = self.home / "proj"
        self.project.mkdir()
        # Pretend self.home is the user's home so the global-refusal logic is
        # exercised against a controlled directory.
        self._patch = mock.patch.object(install.Path, "home", return_value=self.home)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def _settings(self):
        return self.project / ".claude" / "settings.json"


class TestGlobalRefusal(InstallTestBase):
    def test_refuses_home(self):
        with self.assertRaises(InstallError):
            install.plan(self.home)

    def test_refuses_home_dot_claude(self):
        with self.assertRaises(InstallError):
            install.plan(self.home / ".claude")

    def test_allows_project(self):
        merged, changes = install.plan(self.project)
        self.assertIn("hooks", merged)
        self.assertEqual(len(changes), 3)


class TestPlanIsPure(InstallTestBase):
    def test_plan_writes_nothing(self):
        install.plan(self.project)
        self.assertFalse(self._settings().exists())

    def test_plan_lists_all_three_events(self):
        _merged, changes = install.plan(self.project)
        joined = "\n".join(changes)
        for event in ("PreToolUse", "Stop", "UserPromptSubmit"):
            self.assertIn(event, joined)
        self.assertTrue(all(c.startswith("+") for c in changes))


class TestApply(InstallTestBase):
    def test_apply_writes_settings(self):
        path = install.apply(self.project)
        self.assertTrue(path.exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        events = data["hooks"]
        self.assertEqual(set(events), {"PreToolUse", "Stop", "UserPromptSubmit"})
        # PreToolUse carries the Bash-inclusive matcher
        self.assertIn("Bash", events["PreToolUse"][0]["matcher"])
        self.assertIn(
            "phases_oss.hooks.pre_tool_use",
            events["PreToolUse"][0]["hooks"][0]["command"],
        )

    def test_apply_is_idempotent(self):
        install.apply(self.project)
        install.apply(self.project)
        data = json.loads(self._settings().read_text(encoding="utf-8"))
        # exactly one group per event, no duplicates
        for event in ("PreToolUse", "Stop", "UserPromptSubmit"):
            self.assertEqual(len(data["hooks"][event]), 1)
        _merged, changes = install.plan(self.project)
        self.assertTrue(all(c.startswith("=") for c in changes))

    def test_apply_preserves_existing_settings(self):
        claude = self.project / ".claude"
        claude.mkdir(parents=True)
        existing = {
            "model": "some-model",
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo keep-me"}]}
                ]
            },
        }
        (claude / "settings.json").write_text(json.dumps(existing), encoding="utf-8")

        install.apply(self.project)
        data = json.loads(self._settings().read_text(encoding="utf-8"))
        self.assertEqual(data["model"], "some-model")
        commands = [h["command"] for g in data["hooks"]["PreToolUse"] for h in g["hooks"]]
        self.assertIn("echo keep-me", commands)  # original kept
        self.assertTrue(any("phases_oss.hooks.pre_tool_use" in c for c in commands))  # ours added

    def test_malformed_settings_refused_not_overwritten(self):
        claude = self.project / ".claude"
        claude.mkdir(parents=True)
        raw = "{ not json"
        (claude / "settings.json").write_text(raw, encoding="utf-8")
        with self.assertRaises(InstallError):
            install.apply(self.project)
        # the user's (broken) file is left exactly as it was, not clobbered
        self.assertEqual((claude / "settings.json").read_text(encoding="utf-8"), raw)

    def test_non_dict_root_refused(self):
        claude = self.project / ".claude"
        claude.mkdir(parents=True)
        (claude / "settings.json").write_text("[1, 2, 3]", encoding="utf-8")
        with self.assertRaises(InstallError):
            install.plan(self.project)

    def test_stop_and_ups_commands_have_no_matcher(self):
        install.apply(self.project)
        data = json.loads(self._settings().read_text(encoding="utf-8"))
        for event, module in (
            ("Stop", "phases_oss.hooks.stop"),
            ("UserPromptSubmit", "phases_oss.hooks.user_prompt_submit"),
        ):
            group = data["hooks"][event][0]
            self.assertNotIn("matcher", group)
            self.assertIn(module, group["hooks"][0]["command"])

    def test_global_settings_untouched_after_apply(self):
        install.apply(self.project)
        # the global config (under the mocked home) must not have been created
        self.assertFalse((self.home / ".claude" / "settings.json").exists())

    def test_symlinked_claude_to_global_refused(self):
        global_claude = self.home / ".claude"
        global_claude.mkdir()
        (global_claude / "settings.json").write_text('{"model": "precious"}', encoding="utf-8")
        link = self.project / ".claude"
        try:
            link.symlink_to(global_claude, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not permitted on this host")
        with self.assertRaises(InstallError):
            install.apply(self.project)
        # the global file is intact
        self.assertEqual(
            json.loads((global_claude / "settings.json").read_text(encoding="utf-8")),
            {"model": "precious"},
        )


class TestMain(InstallTestBase):
    def test_main_dry_run_writes_nothing(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = install.main([str(self.project)])
        self.assertEqual(rc, 0)
        self.assertFalse(self._settings().exists())
        self.assertIn("Dry-run", out.getvalue())

    def test_main_apply_writes(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = install.main([str(self.project), "--apply"])
        self.assertEqual(rc, 0)
        self.assertTrue(self._settings().exists())

    def test_main_refuses_global(self):
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            rc = install.main([str(self.home)])
        self.assertEqual(rc, 2)
        self.assertIn("refused", err.getvalue())


if __name__ == "__main__":
    unittest.main()
