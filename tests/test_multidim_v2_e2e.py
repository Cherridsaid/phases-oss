"""End-to-end and guarantee tests for the bundled Multidim v2 contract.

Three guarantees, complementary to the unit tests:

* **E2E over stdio**: drive the REAL entry point (``python -m phases_oss.multidim``)
  as a subprocess, exchanging JSON-RPC lines, so the contract is proven from the
  actual process boundary, not only through in-process function calls;
* **golden regression**: a frozen (frame, analysis) pair must always yield the
  same ACCEPT verdict and a stable frame_hash -- the bundle's behaviour is
  pinned as a non-regression reference (true cross-engine parity with the
  canonical engine cannot be checked in-package: it lives outside this package);
* **package neutrality / tightness**: the SHIPPED base contexts carry none of
  the personal contexts of the private installation, and the store never
  resolves to the personal ``~/.multidim``.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from phases_oss.multidim import base_contexts, frames, store


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


# A full MCP handshake, prepended to every session: a complete ``initialize``
# request (protocolVersion, capabilities, clientInfo) followed by the
# ``notifications/initialized`` notification, exactly as a real MCP client does.
def _handshake():
    return [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "phases-oss-e2e-test", "version": "1.0.0"},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},  # no id
    ]


def _run_server(requests, home, fake_home=None):
    """Spawn the real entry point and feed it one JSON-RPC request per line.

    Prepends a full MCP handshake (initialize + notifications/initialized) so
    operational calls run only after initialization, as a real client would.
    Returns the parsed JSON responses (notifications produce none). ``home``
    isolates the store under a temp PHASES_OSS_HOME. ``fake_home`` (when given)
    replaces HOME and USERPROFILE so the personal ``~/.multidim`` path resolves
    into a controlled temp dir -- proving no write ever lands in the real one.
    """
    requests = _handshake() + list(requests)
    env = dict(os.environ)
    if home is not None:
        env["PHASES_OSS_HOME"] = str(home)
    else:
        # exercise the DEFAULT data-dir path: drop the override and every
        # platform data-dir hint so resolution falls back to the sandboxed home
        for var in ("PHASES_OSS_HOME", "LOCALAPPDATA", "APPDATA", "XDG_DATA_HOME"):
            env.pop(var, None)
    if fake_home is not None:
        # point every "home" resolver at the sandbox: Path.home() reads these
        env["HOME"] = str(fake_home)
        env["USERPROFILE"] = str(fake_home)
    # ensure the in-tree src/ is importable in the child without an install
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests)
    proc = subprocess.run(
        [sys.executable, "-m", "phases_oss.multidim"],
        input=payload, capture_output=True, text=True, encoding="utf-8", env=env,
        timeout=60,
    )
    responses = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line:
            responses.append(json.loads(line))
    return responses, proc


def _tool_call(msg_id, name, arguments):
    return {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}


def _result_text(resp):
    return resp["result"]["content"][0]["text"]


class TestStdioE2E(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="phases-e2e-")
        self.home = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _by_id(self, responses):
        return {r.get("id"): r for r in responses}

    def test_initialize_and_tools_list_over_stdio(self):
        reqs = [{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
        responses, proc = _run_server(reqs, self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        by_id = self._by_id(responses)
        # the handshake's initialize (id 0) answered, the notification did not
        self.assertEqual(by_id[0]["result"]["serverInfo"]["name"], "multidim")
        names = {t["name"] for t in by_id[2]["result"]["tools"]}
        self.assertEqual(names, {"multidim_analyze", "multidim_contexts",
                                 "multidim_validate", "multidim_learn"})

    def test_analyze_v2_then_validate_over_stdio(self):
        analysis = {
            "facts": [{"fact_id": "F1", "statement": "the loader parses config first"}],
            "hypotheses": [{"hypothesis_id": "H1", "statement": "seeks dominate boot"}],
            "alternatives": [{"alternative_id": "A1", "statement": "handshake dominates"}],
            "cross_talk": {"tensions": ["speed vs durability"],
                           "blind_spots": ["no prod data"]},
            "synthesis": {"statement": "seeks remain the suspect", "references": ["F1", "H1"]},
        }
        # 1) get a v2 frame, 2) validate the filled analysis against it
        reqs = [_tool_call(2, "multidim_analyze",
                           {"subject": "a plain subject", "context": "generic",
                            "depth": "core", "format": "v2"})]
        responses, proc = _run_server(reqs, self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        frame = json.loads(_result_text(self._by_id(responses)[2]))
        self.assertEqual(frame["analysis_schema_version"], 2)
        self.assertIn("frame_hash", frame)

        reqs2 = [_tool_call(2, "multidim_validate",
                            {"frame": frame, "analysis": analysis})]
        responses2, proc2 = _run_server(reqs2, self.home)
        self.assertEqual(proc2.returncode, 0, proc2.stderr)
        verdict = json.loads(_result_text(self._by_id(responses2)[2]))
        self.assertEqual(verdict["verdict"], "ACCEPT", verdict)
        self.assertEqual(verdict["frame_hash"], frame["frame_hash"])

    def test_learn_persists_isolated_and_never_touches_personal_store(self):
        # sandbox the personal-store resolver too: HOME/USERPROFILE -> fake dir
        with tempfile.TemporaryDirectory(prefix="phases-fakehome-") as fake_home:
            reqs = [_tool_call(2, "multidim_learn",
                               {"context": "incident_review", "keywords": ["outage"]})]
            responses, proc = _run_server(reqs, self.home, fake_home=fake_home)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(self._by_id(responses)[2]["result"].get("isError"))
            # the store was written under the isolated PHASES_OSS_HOME ...
            store_files = list(Path(self.home).rglob("store.json"))
            self.assertTrue(store_files, "learn did not persist under PHASES_OSS_HOME")
            data = json.loads(store_files[0].read_text(encoding="utf-8"))
            self.assertIn("incident_review",
                          [c.get("name") for c in data.get("contexts", [])])
            # ... and NOTHING was written into the personal ~/.multidim path,
            # even though its resolver now points inside the sandbox
            leaked = list(Path(fake_home).rglob(".multidim"))
            self.assertEqual(leaked, [], "a .multidim path was created: %s" % leaked)

    def test_default_path_without_override_never_touches_personal_store(self):
        # NO PHASES_OSS_HOME: the DEFAULT data dir is used. Even then, with the
        # personal-store resolver sandboxed, nothing lands in ~/.multidim.
        with tempfile.TemporaryDirectory(prefix="phases-fakehome-") as fake_home:
            reqs = [_tool_call(2, "multidim_learn",
                               {"context": "incident_review", "keywords": ["outage"]})]
            responses, proc = _run_server(reqs, home=None, fake_home=fake_home)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(self._by_id(responses)[2]["result"].get("isError"))
            # a store was created somewhere under the sandbox (default path) ...
            store_files = list(Path(fake_home).rglob("store.json"))
            self.assertTrue(store_files, "default path did not persist a store")
            # ... in the dedicated phases-oss dir, never in a .multidim dir
            self.assertTrue(all(".multidim" not in str(p) for p in store_files),
                            "default store resolved into a .multidim path: %s" % store_files)
            self.assertEqual(list(Path(fake_home).rglob(".multidim")), [])

    def test_bad_analysis_rejected_over_stdio(self):
        reqs = [_tool_call(2, "multidim_analyze",
                           {"subject": "s", "context": "generic", "depth": "core",
                            "format": "v2"})]
        responses, _ = _run_server(reqs, self.home)
        frame = json.loads(_result_text(self._by_id(responses)[2]))
        holey = {"facts": [{"fact_id": "F1", "statement": "x"}]}  # missing sections
        reqs2 = [_tool_call(2, "multidim_validate", {"frame": frame, "analysis": holey})]
        responses2, _ = _run_server(reqs2, self.home)
        verdict = json.loads(_result_text(self._by_id(responses2)[2]))
        self.assertEqual(verdict["verdict"], "REJECT")


class TestGoldenRegression(unittest.TestCase):
    """Pin the bundle's behaviour: a frozen frame+analysis -> stable verdict.

    Uses an in-memory neutral store (never the on-disk one) so the golden is
    hermetic and independent of any local state.
    """

    def _neutral_store(self):
        s = {"version": base_contexts.BASE_VERSION,
             "contexts": base_contexts.base_contexts()}
        store.migrate_additive(s)
        return s

    GOLDEN_ANALYSIS = {
        "facts": [{"fact_id": "F1", "statement": "the loader parses config before boot"}],
        "hypotheses": [{"hypothesis_id": "H1",
                        "statement": "startup latency comes from disk seeks"}],
        "alternatives": [{"alternative_id": "A1",
                          "statement": "network handshake dominates the delay"}],
        "cross_talk": {"tensions": ["speed versus durability"],
                       "blind_spots": ["no production data"]},
        "synthesis": {"statement": "disk seeks remain the prime suspect",
                      "references": ["F1", "H1"]},
    }

    # Frozen golden hash of build_frame(neutral store, generic, "the golden
    # subject", score 0, core). Pinned literally so ANY change to the shipped
    # contract (axes, sub-lenses, validation rules, blacklist, host hints)
    # breaks this test loudly instead of silently. Regenerate deliberately
    # only when the contract change is intended.
    GOLDEN_FRAME_HASH = "c77840e5df96fbd95286f016fd2f81e092626ce0642de18e8267e0d0c00543e8"

    def test_golden_frame_hash_matches_frozen_constant(self):
        s = self._neutral_store()
        ctx = store.find_context(s, "generic")
        frame = frames.build_frame(s, ctx, "the golden subject", 0, "core")
        # compare to a LITERAL frozen value: catches a real contract regression,
        # not merely that two rebuilds agree
        self.assertEqual(frame["frame_hash"], self.GOLDEN_FRAME_HASH,
                         "shipped v2 contract changed; regenerate the golden "
                         "deliberately if this change is intended")
        # and the hash is self-verifiable on the complete frame
        self.assertEqual(frames.frame_hash_of(frame), frame["frame_hash"])

    def test_golden_verdict_is_accept(self):
        from phases_oss.multidim import validate
        s = self._neutral_store()
        ctx = store.find_context(s, "generic")
        frame = frames.build_frame(s, ctx, "the golden subject", 0, "core")
        verdict = validate.validate_analysis(frame, self.GOLDEN_ANALYSIS)
        self.assertEqual(verdict["verdict"], "ACCEPT", verdict)
        # every required section resolved to ACCEPT, none missing
        by_section = {r["section"]: r["verdict"] for r in verdict["section_results"]}
        for section in frame["required_sections"]:
            self.assertEqual(by_section.get(section), "ACCEPT",
                             "section {} not ACCEPT: {}".format(section, verdict))


class TestPackageNeutrality(unittest.TestCase):
    # personal contexts that live in the PRIVATE installation and must NEVER be
    # part of the published bundle's shipped base store
    PERSONAL_CONTEXTS = {
        "audit_securite", "seo_local", "droit_belge", "crypto_trading",
        "redaction", "produit_app", "design", "audit_offensif",
    }

    def test_shipped_contexts_are_the_neutral_set_only(self):
        shipped = {c["name"] for c in base_contexts.base_contexts()}
        # the bundle ships neutral, generic-purpose contexts only
        self.assertIn("generic", shipped)
        leaked = shipped & self.PERSONAL_CONTEXTS
        self.assertEqual(leaked, set(),
                         "personal context(s) leaked into the shipped bundle: %s" % leaked)

    def test_shipped_contexts_carry_no_traps(self):
        # learned traps are paid lessons of the private install; none must ship
        for c in base_contexts.base_contexts():
            self.assertEqual(c.get("traps", []), [],
                             "shipped context %s carries traps" % c.get("name"))

    def test_neutrality_guard_scans_shipped_contexts(self):
        # the existing forbidden-token guard must pass on the shipped set
        base_contexts.assert_neutral()  # raises if any personal token is present


class TestConsoleScript(unittest.TestCase):
    def test_phases_multidim_entry_point_is_declared_and_callable(self):
        # __main__ advertises a phases-multidim console script; it must exist in
        # pyproject and point at an importable, callable target
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover -- py<3.11
            self.skipTest("tomllib not available")
        with open(ROOT / "pyproject.toml", "rb") as fh:
            data = tomllib.load(fh)
        scripts = data.get("project", {}).get("scripts", {})
        self.assertEqual(scripts.get("phases-multidim"),
                         "phases_oss.multidim.__main__:main")
        from phases_oss.multidim.__main__ import main as md_main
        self.assertTrue(callable(md_main))


if __name__ == "__main__":
    unittest.main()
