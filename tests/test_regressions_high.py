"""Regression tests for the six HIGH findings of the 2026-07-15 audit.

Each test encodes the FIXED behaviour and therefore FAILS on the pre-fix
tree. One test per finding:

  1. default_runner must never raise UnicodeDecodeError (Windows cp1252).
  2. record_audit must not turn a pending_approval phase active (approve gate).
  3. init_phase must refuse while a phase is still open.
  4. the Multidim MCP server must survive an invalid UTF-8 byte on stdin.
  5. the data gate must redact UPPER_SNAKE env-style secrets.
  6. the Bash hook must not flag `install`/`cp` used as an argument.

Tests 1 and 4 spawn real subprocesses on purpose (the bugs live at the
process boundary); the rest are in-process.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from phases_oss import data_gate, phases
from phases_oss.hooks import pre_tool_use
from phases_oss.phases import PhaseError

_SRC = str(Path(__file__).resolve().parent.parent / "src")


def _report(root, name, body_filler):
    """A minimal well-formed review report (>=200 bytes, structured verdict)."""
    path = Path(root) / name
    path.write_text("VERDICT: EXPLOITED\n" + body_filler * 40, encoding="utf-8")
    return str(path)


class TestBug1RunnerEncoding(unittest.TestCase):
    """default_runner decodes proof output as UTF-8, never via the locale."""

    def test_utf8_output_survives_and_is_not_mojibake(self):
        cmd = '"%s" -c "import sys; sys.stdout.buffer.write(\'caf\\u00e9 \\u274c ok\'.encode(\'utf-8\'))"' % sys.executable
        with tempfile.TemporaryDirectory() as tmp:
            code, out = phases.default_runner(cmd, Path(tmp))
        self.assertEqual(code, 0)
        self.assertIn("café", out, "UTF-8 output must decode cleanly, got %r" % out)
        self.assertIn("❌", out)

    def test_invalid_bytes_never_raise(self):
        cmd = '"%s" -c "import sys; sys.stdout.buffer.write(b\'ok \\xff\\xfe raw\')"' % sys.executable
        with tempfile.TemporaryDirectory() as tmp:
            try:
                code, out = phases.default_runner(cmd, Path(tmp))
            except UnicodeDecodeError:
                self.fail("default_runner raised UnicodeDecodeError on invalid bytes")
        self.assertEqual(code, 0)
        self.assertIn("ok", out)


class TestBug2ReopenCannotSkipApprove(unittest.TestCase):
    """No path may turn pending_approval into active without approve()."""

    def test_record_audit_requires_active_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            phases.init_phase(root, objective="t", files_allowed=["a.py"],
                              proof_command="pytest tests/", level=1)
            r1 = _report(root, "r1.md", "exploited finding ")
            with self.assertRaises(PhaseError):
                phases.record_audit(root, reports=[r1], open_exploited=1)
            state = phases.load_state(root)
            self.assertEqual(state.data["status"], "pending_approval",
                             "record_audit on a pending phase must not change status")
            self.assertIsNone(state.data["approved_at"])

    def test_full_bypass_circuit_is_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            phases.init_phase(root, objective="t", files_allowed=["a.py"],
                              proof_command="pytest tests/", level=1)
            r1 = _report(root, "r1.md", "exploited finding ")
            with self.assertRaises(PhaseError):
                phases.record_audit(root, reports=[r1], open_exploited=1)
            # prove refuses too: the phase was never approved.
            with self.assertRaises(PhaseError):
                phases.prove(root, runner=lambda c, w, env=None: (0, "ok"))
            with self.assertRaises(PhaseError):
                phases.close(root, lesson="should never close")
            state = phases.load_state(root)
            self.assertNotEqual(state.data["status"], "complete")


class TestBug3InitRefusesOverOpenPhase(unittest.TestCase):
    """init_phase over an open phase must refuse, not silently overwrite."""

    def test_init_refuses_on_pending_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            phases.init_phase(root, objective="phase A", files_allowed=["a.py"],
                              proof_command="pytest tests/", level=1)
            with self.assertRaises(PhaseError):
                phases.init_phase(root, objective="phase B", files_allowed=["b.py"],
                                  proof_command="pytest tests/x.py", level=0)
            state = phases.load_state(root)
            self.assertEqual(state.data["objective"], "phase A")

    def test_init_refuses_on_active_phase_and_reset_unblocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            phases.init_phase(root, objective="phase A", files_allowed=["a.py"],
                              proof_command="pytest tests/", level=2, full_suite=True)
            phases.approve(root)
            with self.assertRaises(PhaseError) as ctx:
                phases.init_phase(root, objective="phase B", files_allowed=["b.py"],
                                  proof_command="pytest tests/x.py", level=0)
            self.assertIn("reset", str(ctx.exception).lower(),
                          "the refusal must point to the reset escape hatch")
            phases.reset(root)
            phases.init_phase(root, objective="phase B", files_allowed=["b.py"],
                              proof_command="pytest tests/x.py", level=0)
            self.assertEqual(phases.load_state(root).data["objective"], "phase B")


class TestBug4ServerSurvivesInvalidUtf8(unittest.TestCase):
    """One invalid UTF-8 byte on stdin must not kill the MCP server."""

    def test_server_answers_after_invalid_byte(self):
        ping = lambda i: (json.dumps({"jsonrpc": "2.0", "id": i, "method": "ping"})
                          .encode("utf-8") + b"\n")
        payload = ping(1) + b"\xff\xfe\n" + ping(2)
        env = dict(os.environ)
        env["PYTHONPATH"] = _SRC
        proc = subprocess.run([sys.executable, "-m", "phases_oss.multidim"],
                              input=payload, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env, timeout=60)
        self.assertEqual(proc.returncode, 0,
                         "server died: rc=%d stderr=%r"
                         % (proc.returncode, proc.stderr[-300:]))
        ids = []
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            if line.strip():
                ids.append(json.loads(line).get("id"))
        self.assertIn(1, ids, "ping sent before the bad byte must be answered")
        self.assertIn(2, ids, "ping sent after the bad byte must be answered")


class TestBug5EnvStyleSecretsRedacted(unittest.TestCase):
    """UPPER_SNAKE env-style secret assignments must be redacted."""

    SAMPLES = [
        "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENGbPxRfiCYEXAMPLEKEY",
        "AWS_SESSION_TOKEN=FwoGZXIvYXdzEBEaDDEXAMPLEEXAMPLE",
        "OPENAI_API_KEY=sk-" + "proj-abc123def456ghi789",
        "ANTHROPIC_API_KEY=sk-" + "ant-api03-abcdef123456",
        "SLACK_TOKEN=xox" + "b-123456789-abcdefghij",
        "STRIPE_SECRET_KEY=sk_" + "live_abcdef123456",
        "GOOGLE_API_KEY=AI" + "zaSyAbCdEf123456789",
        "DB_PASSWORD=hunter2secret",
        "MY_APP_SECRET: some-long-secret-value",
        "export CUSTOM_TOKEN=abcdef123456789012345",
    ]

    def test_env_style_secrets_are_redacted(self):
        for sample in self.SAMPLES:
            with self.subTest(sample=sample.split("=")[0].split(":")[0]):
                redacted, actions = data_gate.redact(sample)
                self.assertNotEqual(redacted, sample,
                                    "leaked in cleartext: %r" % sample)
                self.assertTrue(actions, "no redaction recorded for %r" % sample)

    def test_no_false_positive_on_prose(self):
        prose = "the token bucket algorithm limits requests; a password manager helps"
        redacted, _ = data_gate.redact(prose)
        self.assertEqual(redacted, prose, "plain prose must not be redacted")


class TestBug6InstallAsArgumentIsNotAWrite(unittest.TestCase):
    """`install`/`cp` as an ARGUMENT must not deny; real writes must still deny."""

    ALLOWED = [
        "pip install requests",
        "pip3 install -r requirements.txt",
        "npm install",
        "cargo install ripgrep",
        "apt-get install -y jq",
        "man cp",
        "grep -r install .",
        "git log",
    ]
    DENIED = [
        "cp a.py b.py",
        "mv a.py b.py",
        "tee out.py",
        "install -m 644 a b",
        "sed -i s/a/b/ f.py",
    ]

    def test_package_manager_installs_allowed(self):
        for cmd in self.ALLOWED:
            with self.subTest(cmd=cmd):
                self.assertFalse(pre_tool_use.bash_writes_file(cmd),
                                 "false positive on: %r" % cmd)

    def test_real_write_tools_still_denied(self):
        for cmd in self.DENIED:
            with self.subTest(cmd=cmd):
                self.assertTrue(pre_tool_use.bash_writes_file(cmd),
                                "false negative on: %r" % cmd)


if __name__ == "__main__":
    unittest.main()
