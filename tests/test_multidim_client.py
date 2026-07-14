"""Tests for the stdio client that wires Multidim into the /phases engine."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from phases_oss.multidim import client
from phases_oss import phases


class _IsolatedHome(unittest.TestCase):
    def setUp(self):
        # isolate the server's store to a temp dir (the client spawns a real
        # server subprocess that may seed a store)
        self._tmp = tempfile.TemporaryDirectory(prefix="phases-client-")
        self._prev = os.environ.get("PHASES_OSS_HOME")
        os.environ["PHASES_OSS_HOME"] = self._tmp.name

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("PHASES_OSS_HOME", None)
        else:
            os.environ["PHASES_OSS_HOME"] = self._prev
        self._tmp.cleanup()


class TestAnalyze(_IsolatedHome):
    def test_analyze_returns_v2_frame(self):
        frame = client.analyze("review a code change for regressions", depth="core")
        self.assertEqual(frame["analysis_schema_version"], 2)
        self.assertTrue(frame["frame_hash"])
        self.assertTrue(frame["axes"])

    def test_analyze_rejects_bad_depth(self):
        with self.assertRaises(client.MultidimClientError):
            client.analyze("x", depth="nope")

    def test_analyze_rejects_empty_subject(self):
        with self.assertRaises(client.MultidimClientError):
            client.analyze("   ", depth="core")


class TestMalformedResponses(_IsolatedHome):
    def test_non_object_response_raises(self):
        original = client._exchange
        client._exchange = lambda reqs, timeout: [42]  # not a JSON-RPC object
        try:
            with self.assertRaises(client.MultidimClientError):
                client.analyze("x", depth="core")
        finally:
            client._exchange = original


class TestUnnamedAxes(_IsolatedHome):
    def test_prepare_rejects_frame_without_named_axes(self):
        # a frame whose axes carry no usable name must fail early with a clear
        # error, not an empty axes list that init rejects far downstream
        original = client._exchange
        frame = {"analysis_schema_version": 2, "frame_hash": "h",
                 "frame_id": "frame_x", "context": {"name": "generic"},
                 "store_version": 2, "axes": [{"bad": "value"}]}
        client._exchange = lambda reqs, timeout: [
            {"id": 1, "result": {"content": [{"type": "text",
                                              "text": json.dumps(frame)}]}}]
        try:
            with self.assertRaises(client.MultidimClientError):
                client.analyze("x", depth="core")
        finally:
            client._exchange = original

    def test_prepare_rejects_non_string_axis_name(self):
        # axis name that is not a non-empty string (e.g. 0) must fail in
        # analyze, never silently vanish and confuse init downstream
        original = client._exchange
        frame = {"analysis_schema_version": 2, "frame_hash": "h",
                 "frame_id": "frame_y", "context": {"name": "generic"},
                 "store_version": 2, "axes": [{"name": 0}]}
        client._exchange = lambda reqs, timeout: [
            {"id": 1, "result": {"content": [{"type": "text",
                                              "text": json.dumps(frame)}]}}]
        try:
            with self.assertRaises(client.MultidimClientError):
                client.analyze("x", depth="core")
        finally:
            client._exchange = original


class TestPrepare(_IsolatedHome):
    def test_prepare_persists_artifact_and_returns_metadata(self):
        with tempfile.TemporaryDirectory(prefix="phases-root-") as root:
            root_path = Path(root)
            meta = client.prepare("cable the analysis gate", level=2, root=root_path)
            # depth derives from the level (2 -> deep)
            self.assertEqual(meta["depth"], "deep")
            self.assertTrue(meta["axes"])
            self.assertTrue(meta["analysis_ref"].startswith("artifact://multidim/"))
            # the artifact was written under <root>/.phases/analysis/
            artifact = Path(meta["artifact_path"])
            self.assertTrue(artifact.exists())
            self.assertIn(".phases", artifact.parts)
            frame = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(frame["analysis_schema_version"], 2)

    def test_prepare_metadata_satisfies_the_phase_gate(self):
        # the whole point: prepare's output must be accepted by init's analysis
        # gate at the same level, with no external MCP
        with tempfile.TemporaryDirectory(prefix="phases-root-") as root:
            root_path = Path(root)
            meta = client.prepare("wire the gate end to end", level=2, root=root_path)
            phase = phases.init_phase(
                root_path,
                objective="prove the prepared analysis is accepted by init",
                files_allowed=["src/phases_oss/phases.py"],
                proof_command="python run_tests.py",
                level=2, full_suite=True,
                require_analysis=True,
                analysis_context=meta["context"],
                analysis_depth=meta["depth"],
                analysis_axes=meta["axes"],
                analysis_ref=meta["analysis_ref"],
            )
            self.assertTrue(phase.data["analysis"]["used"])
            self.assertEqual(phase.data["analysis"]["analysis_ref"], meta["analysis_ref"])

    def test_prepare_rejects_bad_level(self):
        with self.assertRaises(client.MultidimClientError):
            client.prepare("x", level=9)

    def test_prepare_rejects_depth_below_level(self):
        # frame depth 'core' while level 3 wants 'full' -> reject, never let the
        # gate accept an under-depth analysis
        original = client.analyze
        client.analyze = lambda *a, **k: {
            "analysis_schema_version": 2, "frame_hash": "h", "axes": [{"name": "A"}],
            "frame_id": "frame_d", "context": {"name": "generic"},
            "store_version": 2, "depth": "core"}
        try:
            with tempfile.TemporaryDirectory(prefix="phases-root-") as root:
                with self.assertRaises(client.MultidimClientError):
                    client.prepare("x", level=3, root=Path(root))
        finally:
            client.analyze = original

    def test_prepare_rejects_non_string_context_name(self):
        # a non-string context.name must fail here, not crash init with
        # AttributeError on .strip()
        original = client.analyze
        client.analyze = lambda *a, **k: {
            "analysis_schema_version": 2, "frame_hash": "h", "axes": [{"name": "A"}],
            "frame_id": "frame_ctx", "context": {"name": 7}, "store_version": 2, "depth": "deep"}
        try:
            with tempfile.TemporaryDirectory(prefix="phases-root-") as root:
                with self.assertRaises(client.MultidimClientError):
                    client.prepare("x", level=2, root=Path(root))
        finally:
            client.analyze = original

    def test_prepare_falls_back_to_forced_context(self):
        # if the frame's context name is unusable but a context was forced, use it
        original = client.analyze
        client.analyze = lambda *a, **k: {
            "analysis_schema_version": 2, "frame_hash": "h", "axes": [{"name": "A"}],
            "frame_id": "frame_fb", "context": {"name": None}, "store_version": 2, "depth": "deep"}
        try:
            with tempfile.TemporaryDirectory(prefix="phases-root-") as root:
                meta = client.prepare("x", level=2, context="my_ctx", root=Path(root))
                self.assertEqual(meta["context"], "my_ctx")
        finally:
            client.analyze = original

    def test_prepare_rejects_non_numeric_store_version(self):
        original = client.analyze
        # cover both a non-numeric string and an infinite float (int(inf) raises
        # OverflowError, a distinct failure mode)
        for bad in ("x", float("inf")):
            client.analyze = lambda *a, _bad=bad, **k: {
                "analysis_schema_version": 2, "frame_hash": "h", "axes": [{"name": "A"}],
                "frame_id": "frame_abc", "context": {"name": "generic"},
                "store_version": _bad, "depth": "deep"}
            try:
                with tempfile.TemporaryDirectory(prefix="phases-root-") as root:
                    with self.assertRaises(client.MultidimClientError):
                        client.prepare("x", level=2, root=Path(root))
            finally:
                client.analyze = original

    def test_prepare_rejects_store_symlinked_outside_root(self):
        original = client.analyze
        client.analyze = lambda *a, **k: {
            "analysis_schema_version": 2, "frame_hash": "h", "axes": [{"name": "A"}],
            "frame_id": "frame_sym", "context": {"name": "generic"}, "store_version": 2, "depth": "deep"}
        try:
            with tempfile.TemporaryDirectory(prefix="phases-root-") as root, \
                 tempfile.TemporaryDirectory(prefix="phases-out-") as outside:
                root_path = Path(root)
                (root_path / ".phases").mkdir()
                link = root_path / ".phases" / "analysis"
                try:
                    link.symlink_to(outside, target_is_directory=True)
                except (OSError, NotImplementedError):
                    self.skipTest("symlinks not permitted on this host")
                with self.assertRaises(client.MultidimClientError):
                    client.prepare("x", level=2, root=root_path)
        finally:
            client.analyze = original

    def test_prepare_rejects_unsafe_frame_id(self):
        # a server returning a traversal frame_id must never write outside the
        # store dir
        original = client.analyze
        client.analyze = lambda *a, **k: {
            "analysis_schema_version": 2, "frame_hash": "h", "axes": [{"name": "A"}],
            "frame_id": "../escaped", "context": {"name": "generic"}, "store_version": 2, "depth": "deep"}
        try:
            with tempfile.TemporaryDirectory(prefix="phases-root-") as root:
                with self.assertRaises(client.MultidimClientError):
                    client.prepare("x", level=2, root=Path(root))
        finally:
            client.analyze = original


class TestFailClosed(_IsolatedHome):
    def test_unreachable_server_raises(self):
        # point the client at a command that cannot be a Multidim server
        original = client._server_command
        client._server_command = lambda: [__import__("sys").executable, "-c", "raise SystemExit(3)"]
        try:
            with self.assertRaises(client.MultidimClientError):
                client.analyze("x", depth="core")
        finally:
            client._server_command = original

    def test_cli_prepare_analysis_prints_metadata(self):
        with tempfile.TemporaryDirectory(prefix="phases-cli-") as root:
            rc = phases.main(["--root", root, "prepare-analysis",
                              "--subject", "cli smoke of the analysis prep", "--level", "1"])
            self.assertEqual(rc, 0)
            # the artifact store was created under the given root
            store = Path(root) / ".phases" / "analysis"
            self.assertTrue(any(store.glob("*.json")))


if __name__ == "__main__":
    unittest.main()
