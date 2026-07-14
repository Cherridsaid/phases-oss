"""Tests for the standalone Multidim MCP server (protocol + grid + stdio loop)."""

import io
import json
import os
import tempfile
import unittest

from phases_oss.multidim import base_contexts, server


def _neutral_store():
    return {"version": base_contexts.BASE_VERSION, "contexts": base_contexts.base_contexts()}


class TestProtocol(unittest.TestCase):
    def setUp(self):
        # Some tools/call cases (multidim_learn) persist the store; isolate that
        # write to a temp dir so tests never touch the real data directory.
        self._tmp = tempfile.TemporaryDirectory(prefix="phases-mdproto-")
        self._prev = os.environ.get("PHASES_OSS_HOME")
        os.environ["PHASES_OSS_HOME"] = self._tmp.name
        self.store = _neutral_store()

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("PHASES_OSS_HOME", None)
        else:
            os.environ["PHASES_OSS_HOME"] = self._prev
        self._tmp.cleanup()

    def test_initialize_negotiation(self):
        resp = server.handle_message(self.store, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                                  "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(resp["id"], 1)
        self.assertEqual(resp["result"]["serverInfo"]["name"], "multidim")
        self.assertIn("tools", resp["result"]["capabilities"])
        self.assertEqual(resp["result"]["protocolVersion"], "2025-06-18")

    def test_wrong_jsonrpc_version_is_invalid_request(self):
        resp = server.handle_message(self.store,
                                     {"jsonrpc": "1.0", "id": 1, "method": "ping"})
        self.assertEqual(resp["error"]["code"], -32600)

    def test_non_string_method_is_invalid_request(self):
        resp = server.handle_message(self.store,
                                     {"jsonrpc": "2.0", "id": 1, "method": 42})
        self.assertEqual(resp["error"]["code"], -32600)

    def test_unsupported_protocol_version_negotiates_down(self):
        # an unsupported requested version must NOT be echoed back (that would
        # falsely claim agreement): the server answers its own supported version
        resp = server.handle_message(self.store, {"jsonrpc": "2.0", "id": 1,
                                                  "method": "initialize",
                                                  "params": {"protocolVersion": "unsupported-1999"}})
        self.assertEqual(resp["result"]["protocolVersion"], server.PROTOCOL_VERSION)

    def test_ping(self):
        resp = server.handle_message(self.store, {"jsonrpc": "2.0", "id": 2, "method": "ping"})
        self.assertEqual(resp["result"], {})

    def test_tools_list_has_four_tools(self):
        resp = server.handle_message(self.store, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertEqual(names, {"multidim_analyze", "multidim_contexts",
                                 "multidim_validate", "multidim_learn"})

    def test_notification_returns_none(self):
        self.assertIsNone(server.handle_message(self.store, {"method": "notifications/initialized"}))

    def test_tool_call_reloads_store_from_disk(self):
        # a learn persisted by "another server" must be visible to THIS server's
        # next tool call, because tools/call reloads the store from disk
        from phases_oss.multidim import store as store_mod
        store_mod.mutate(lambda s: s["contexts"].append(
            {"name": "outside_ctx", "description": "d", "keywords": [],
             "axes": [], "traps": []}))
        # our in-memory self.store does NOT have it yet
        self.assertIsNone(store_mod.find_context(self.store, "outside_ctx"))
        resp = server.handle_message(self.store, {"jsonrpc": "2.0", "id": 9,
                                                  "method": "tools/call",
                                                  "params": {"name": "multidim_contexts",
                                                             "arguments": {}}})
        self.assertIn("outside_ctx", resp["result"]["content"][0]["text"])

    def test_explicit_null_id_is_a_request_not_a_notification(self):
        # id present but null is a REQUEST: it must get a response (id null),
        # never be silently dropped as a notification
        resp = server.handle_message(self.store,
                                     {"jsonrpc": "2.0", "id": None, "method": "ping"})
        self.assertIsNotNone(resp)
        self.assertIsNone(resp["id"])
        self.assertEqual(resp["result"], {})

    def test_unknown_method_is_error(self):
        resp = server.handle_message(self.store, {"jsonrpc": "2.0", "id": 4, "method": "nope"})
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32601)

    def test_non_object_params_is_invalid_params(self):
        resp = server.handle_message(self.store, {"jsonrpc": "2.0", "id": 5,
                                                  "method": "tools/list", "params": "oops"})
        self.assertEqual(resp["error"]["code"], -32602)

    def test_non_object_arguments_is_invalid_params(self):
        resp = server.handle_message(self.store, {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                                                  "params": {"name": "multidim_analyze", "arguments": "oops"}})
        self.assertEqual(resp["error"]["code"], -32602)

    def test_learn_with_non_list_keywords_is_iserror_not_crash(self):
        resp = server.handle_message(self.store, {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                                                  "params": {"name": "multidim_learn",
                                                             "arguments": {"context": "x", "keywords": 1}}})
        self.assertTrue(resp["result"].get("isError"))

    def test_learn_rejects_offschema_fields_before_mutation(self):
        # Any off-schema field is refused; nothing is written to the store.
        from phases_oss.multidim import store as store_mod
        before = len(self.store["contexts"])
        for bad in ({"context": "x", "description": 123},
                    {"context": "x", "keywords": [1]},
                    {"context": "x", "axes": [{"name": 5}]},
                    {"context": "x", "axes": [{"name": "A", "sublenses": [2]}]}):
            resp = server.handle_message(self.store, {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                                      "params": {"name": "multidim_learn", "arguments": bad}})
            self.assertTrue(resp["result"].get("isError"))
        self.assertEqual(len(self.store["contexts"]), before)  # nothing mutated

    def test_learned_context_round_trips_through_load(self):
        # A valid learn must produce a context that store._valid_context accepts,
        # so the next load() does NOT reset the store.
        from phases_oss.multidim import store as store_mod
        server.call_tool(self.store, "multidim_learn",
                         {"context": "newdomain", "description": "d", "keywords": ["k1", "k2"],
                          "axes": [{"name": "A", "question": "q?", "sublenses": ["s1"]}]})
        created = store_mod.find_context(self.store, "newdomain")
        self.assertTrue(store_mod._valid_context(created))

    def test_unknown_tool_is_iserror(self):
        resp = server.handle_message(self.store, {"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                                                  "params": {"name": "nope", "arguments": {}}})
        self.assertTrue(resp["result"].get("isError"))


class TestFold(unittest.TestCase):
    # Accented inputs are built with chr() so this test's own source stays ASCII.
    def test_fold_replaces_accents(self):
        self.assertEqual(server.fold("r" + chr(0xE9) + "vision"), "revision")  # revision
        self.assertEqual(server.fold(chr(0xE0) + chr(0xE7)), "ac")             # a-grave, c-cedilla
        self.assertEqual(server.fold(chr(0xF1)), "n")                          # n-tilde

    def test_accented_keyword_matches_folded_subject(self):
        # Keyword and subject chosen NOT to collide with any base context keyword,
        # so the match is unambiguously the learned context (accent folding works).
        store = _neutral_store()
        precision = "pr" + chr(0xE9) + "cision"  # -> folds to "precision"
        store["contexts"].append({
            "name": "customdomain", "description": "d",
            "keywords": [precision],
            "axes": [{"name": "A", "question": "q", "sublenses": ["s"]}],
        })
        c, score = server.detect_context(store, "la " + precision + " du travail")
        self.assertEqual(c["name"], "customdomain")
        self.assertGreaterEqual(score, 1)


class TestAnalyze(unittest.TestCase):
    def setUp(self):
        self.store = _neutral_store()

    def _call(self, args):
        resp = server.handle_message(self.store, {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                                  "params": {"name": "multidim_analyze", "arguments": args}})
        return resp["result"]

    def test_returns_grid(self):
        r = self._call({"subject": "review a diff for a code change"})
        text = r["content"][0]["text"]
        self.assertIn("MULTIDIMENSIONAL GRID", text)
        self.assertIn("[1]", text)
        self.assertNotIn("isError", r)

    def test_auto_detects_context(self):
        text = self._call({"subject": "review this pull request diff for regressions"})["content"][0]["text"]
        self.assertIn("CONTEXT: code_review", text)

    def test_depth_core_has_no_sublenses(self):
        text = self._call({"subject": "anything", "depth": "core"})["content"][0]["text"]
        self.assertNotIn("      - ", text)
        self.assertIn("Briefly relate the axes", text)

    def test_depth_deep_has_sublenses(self):
        text = self._call({"subject": "anything", "depth": "deep"})["content"][0]["text"]
        self.assertIn("      - ", text)
        self.assertIn("Relate each axis", text)

    def test_depth_full_has_recursion(self):
        text = self._call({"subject": "anything", "depth": "full"})["content"][0]["text"]
        self.assertIn("TARGETED RECURSION", text)

    def test_missing_subject_is_error(self):
        r = self._call({})
        self.assertTrue(r.get("isError"))

    def test_unknown_forced_context_is_error(self):
        r = self._call({"subject": "x", "context": "does_not_exist"})
        self.assertTrue(r.get("isError"))

    def test_grid_output_is_neutral(self):
        text = self._call({"subject": "x", "depth": "full"})["content"][0]["text"].lower()
        # the shipped grid must carry no local path / host-tool markers
        for tok in ("c:/users", "/home/", ".multidim", ".mempalace", ".ssh/"):
            self.assertNotIn(tok, text)


class TestStdioLoop(unittest.TestCase):
    """Exercise the real serve() loop over in-memory streams (line-delimited JSON-RPC)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="phases-mdloop-")
        self._prev = os.environ.get("PHASES_OSS_HOME")
        os.environ["PHASES_OSS_HOME"] = self._tmp.name

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("PHASES_OSS_HOME", None)
        else:
            os.environ["PHASES_OSS_HOME"] = self._prev
        self._tmp.cleanup()

    def test_invalid_json_answers_parse_error(self):
        lines = ["{bad json", json.dumps({"jsonrpc": "2.0", "id": 11, "method": "ping"})]
        stdin = io.StringIO("\n".join(lines) + "\n")
        stdout = io.StringIO()
        server.serve(stdin=stdin, stdout=stdout, log_stream=io.StringIO())
        out = [json.loads(l) for l in stdout.getvalue().splitlines() if l.strip()]
        self.assertEqual(out[0]["error"]["code"], -32700)
        self.assertIsNone(out[0]["id"])
        self.assertEqual(out[1]["id"], 11)  # loop kept serving

    def test_non_object_json_is_invalid_request_not_a_crash(self):
        # A bare JSON array decodes but is not a request object: the server must
        # answer -32600 and keep serving the next valid message, never crash.
        lines = ["[]", json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"})]
        stdin = io.StringIO("\n".join(lines) + "\n")
        stdout = io.StringIO()
        rc = server.serve(stdin=stdin, stdout=stdout, log_stream=io.StringIO())
        self.assertEqual(rc, 0)
        out = [json.loads(l) for l in stdout.getvalue().splitlines() if l.strip()]
        self.assertEqual(out[0]["error"]["code"], -32600)
        self.assertIsNone(out[0]["id"])
        self.assertEqual(out[1]["id"], 7)  # loop kept serving

    def test_loop_answers_in_order_and_skips_notifications(self):
        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"method": "notifications/initialized"}),  # no reply
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "multidim_analyze", "arguments": {"subject": "s", "depth": "core"}}}),
        ]
        stdin = io.StringIO("\n".join(lines) + "\n")
        stdout = io.StringIO()
        logs = io.StringIO()
        rc = server.serve(stdin=stdin, stdout=stdout, log_stream=logs)
        # startup log went to the captured stream, not the real stderr
        self.assertIn("started", logs.getvalue())
        self.assertEqual(rc, 0)
        out = [json.loads(l) for l in stdout.getvalue().splitlines() if l.strip()]
        # 3 responses (the notification produced none), ids in order.
        self.assertEqual([m["id"] for m in out], [1, 2, 3])
        self.assertEqual(out[0]["result"]["serverInfo"]["name"], "multidim")
        self.assertIn("MULTIDIMENSIONAL GRID", out[2]["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
