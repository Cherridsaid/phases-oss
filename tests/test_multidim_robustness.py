"""Regression tests for five store/learn robustness defects (audit 2026-07-25).

Each test reproduces, through the real entry points (``call_tool`` /
``handle_message`` / ``load``), a defect that existed on the previous tree:

* D1 -- a stored context WITHOUT ``name`` crashed ``multidim_analyze`` with
  ``KeyError`` once selected by detection;
* D2 -- a stored context WITHOUT ``keywords``/``axes`` crashed
  ``multidim_learn`` with ``KeyError`` (``_valid_context`` accepted what the
  consumers could not handle);
* D3 -- re-sending the same ``axes`` to ``multidim_learn`` appended duplicates
  forever (learn was not idempotent for axes, unlike traps);
* D4 -- learning ``keywords`` on ``generic`` reported success although the
  detector never keyword-matches ``generic`` (silently dead data);
* D5 -- the in-memory reset markers ``_reset_reason``/``_backup`` leaked into
  ``store.json`` when a mutation followed a corrupt-store reset.
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from phases_oss.multidim import server, store


class RobustnessTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="phases-mdrobust-")
        self._prev = os.environ.get("PHASES_OSS_HOME")
        os.environ["PHASES_OSS_HOME"] = self._tmp.name

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("PHASES_OSS_HOME", None)
        else:
            os.environ["PHASES_OSS_HOME"] = self._prev
        self._tmp.cleanup()

    def _write_store(self, mutate):
        """Load the seeded store, apply ``mutate`` to it, write it back raw
        (bypassing save-side sanitisation on purpose: these tests simulate a
        hand-edited store.json)."""
        st = store.load()
        mutate(st)
        path = store.paths.store_path()
        path.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        return path


class TestD1NamelessContext(RobustnessTestBase):
    def test_nameless_context_is_invalid(self):
        # The consumers all rely on context["name"]; a context without a
        # non-empty name must not pass validation.
        self.assertFalse(store._valid_context({"description": "d", "keywords": [],
                                               "axes": [], "traps": []}))
        self.assertFalse(store._valid_context({"name": "", "description": "d",
                                               "keywords": [], "axes": [], "traps": []}))

    def test_store_with_nameless_context_is_backed_up_and_reset(self):
        path = self._write_store(lambda st: st["contexts"].append(
            {"description": "d", "keywords": ["zorglubxyz"], "axes": [], "traps": []}))
        st = store.load()
        self.assertTrue(path.with_name(path.name + ".bak").exists())
        self.assertTrue(all(c.get("name") for c in st["contexts"]))
        # analysis works again after the reset (no KeyError)
        text, is_error = server.call_tool(st, "multidim_analyze",
                                          {"subject": "sujet zorglubxyz", "format": "v2"})
        self.assertFalse(is_error, text)


class TestD2MissingKeywordsAxes(RobustnessTestBase):
    def test_missing_keywords_and_axes_are_migrated_additively(self):
        # Recoverable gaps: completed with [] on load, NO reset, data kept.
        path = self._write_store(lambda st: st["contexts"].append(
            {"name": "nokw", "description": "x", "traps": []}))
        st = store.load()
        ctx = store.find_context(st, "nokw")
        self.assertIsNotNone(ctx, "context must survive the load (no reset)")
        self.assertEqual(ctx["keywords"], [])
        self.assertEqual(ctx["axes"], [])
        self.assertFalse(path.with_name(path.name + ".bak").exists())

    def test_learn_on_migrated_context_no_longer_crashes(self):
        self._write_store(lambda st: st["contexts"].append(
            {"name": "nokw", "description": "x", "traps": []}))
        st = store.load()
        text, is_error = server.call_tool(st, "multidim_learn",
                                          {"context": "nokw", "keywords": ["abc"],
                                           "axes": [{"name": "A", "question": "q"}]})
        self.assertFalse(is_error, text)
        fresh = store.load()
        ctx = store.find_context(fresh, "nokw")
        self.assertIn("abc", ctx["keywords"])
        self.assertEqual([a["name"] for a in ctx["axes"]], ["A"])


class TestD3AxisDeduplication(RobustnessTestBase):
    def test_same_learn_three_times_keeps_one_axis(self):
        ax = [{"name": "AxeA", "question": "q?", "sublenses": ["s1"]}]
        for _ in range(3):
            text, is_error = server.call_tool(store.load(), "multidim_learn",
                                              {"context": "newctx", "axes": ax})
            self.assertFalse(is_error, text)
        ctx = store.find_context(store.load(), "newctx")
        self.assertEqual([a["name"] for a in ctx["axes"]], ["AxeA"])

    def test_resent_axis_updates_in_place(self):
        # Same axis name = same axis: question/sublenses are refreshed,
        # never appended as a sibling duplicate.
        server.call_tool(store.load(), "multidim_learn",
                         {"context": "c", "axes": [{"name": "AxeA", "question": "old"}]})
        server.call_tool(store.load(), "multidim_learn",
                         {"context": "c", "axes": [{"name": "AxeA", "question": "new",
                                                    "sublenses": ["s"]}]})
        ctx = store.find_context(store.load(), "c")
        self.assertEqual(len(ctx["axes"]), 1)
        self.assertEqual(ctx["axes"][0]["question"], "new")
        self.assertEqual(ctx["axes"][0]["sublenses"], ["s"])

    def test_partial_resend_preserves_omitted_fields(self):
        # Review finding (2026-07-25): re-sending {name, question} only must
        # NOT erase the stored sublenses (nor question, when only sublenses
        # are re-sent). Omitted = untouched, provided = refreshed.
        server.call_tool(store.load(), "multidim_learn",
                         {"context": "c", "axes": [{"name": "A", "question": "q0",
                                                    "sublenses": ["s1", "s2"]}]})
        server.call_tool(store.load(), "multidim_learn",
                         {"context": "c", "axes": [{"name": "A", "question": "q1"}]})
        ctx = store.find_context(store.load(), "c")
        self.assertEqual(len(ctx["axes"]), 1)
        self.assertEqual(ctx["axes"][0]["question"], "q1")
        self.assertEqual(ctx["axes"][0]["sublenses"], ["s1", "s2"])
        # symmetric: re-send sublenses only, question survives
        server.call_tool(store.load(), "multidim_learn",
                         {"context": "c", "axes": [{"name": "A", "sublenses": ["s3"]}]})
        ctx = store.find_context(store.load(), "c")
        self.assertEqual(ctx["axes"][0]["question"], "q1")
        self.assertEqual(ctx["axes"][0]["sublenses"], ["s3"])

    def test_legacy_duplicate_axes_are_collapsed_on_learn(self):
        # Review finding (2026-07-25, round 3): a store written by the OLD
        # buggy extend may already hold two axes named 'A'. by_name indexed
        # only one of them, so a re-learn updated one twin and left the other
        # forever. merge_axes now collapses legacy duplicates at the write
        # door: first occurrence keeps its position, later twins fill the
        # fields the survivor lacks, then disappear.
        self._write_store(lambda st: st["contexts"].append(
            {"name": "legacy", "description": "d", "keywords": [], "traps": [],
             "axes": [{"name": "A", "question": "q1", "sublenses": []},
                      {"name": "B", "question": "qb", "sublenses": []},
                      {"name": "A", "question": "", "sublenses": ["s1"]}]}))
        text, is_error = server.call_tool(store.load(), "multidim_learn",
                                          {"context": "legacy",
                                           "axes": [{"name": "A", "question": "q2"}]})
        self.assertFalse(is_error, text)
        ctx = store.find_context(store.load(), "legacy")
        self.assertEqual([a["name"] for a in ctx["axes"]], ["A", "B"])
        # survivor kept its position, took the update, recovered the twin's
        # sublenses it lacked
        self.assertEqual(ctx["axes"][0]["question"], "q2")
        self.assertEqual(ctx["axes"][0]["sublenses"], ["s1"])

    def test_whitespace_only_axis_name_is_refused(self):
        # Review finding (2026-07-25, round 7): '   ' passed the non-empty
        # check unstripped, dodged the dedup (blank names are not identities)
        # and accumulated a duplicate per learn. Refused at the door now.
        text, is_error = server.call_tool(store.load(), "multidim_learn",
                                          {"context": "c", "axes": [{"name": "   "}]})
        self.assertTrue(is_error)
        self.assertIn("name", text)

    def test_axis_name_is_stored_stripped(self):
        # ' A ' and 'A' are one identity: stored stripped, deduped together.
        server.call_tool(store.load(), "multidim_learn",
                         {"context": "c", "axes": [{"name": " A ", "question": "q1"}]})
        server.call_tool(store.load(), "multidim_learn",
                         {"context": "c", "axes": [{"name": "A", "question": "q2"}]})
        ctx = store.find_context(store.load(), "c")
        self.assertEqual([a["name"] for a in ctx["axes"]], ["A"])
        self.assertEqual(ctx["axes"][0]["question"], "q2")

    def test_legacy_unstripped_axis_name_merges_with_stripped(self):
        # Review finding (2026-07-25, round 8): a historical axis stored as
        # ' A ' plus a re-learn of 'A' produced two axes (dedup indexed the
        # unstripped name). One identity now: survivor renamed 'A', updated.
        self._write_store(lambda st: st["contexts"].append(
            {"name": "legacy2", "description": "d", "keywords": [], "traps": [],
             "axes": [{"name": " A ", "question": "q1", "sublenses": ["s"]}]}))
        text, is_error = server.call_tool(store.load(), "multidim_learn",
                                          {"context": "legacy2",
                                           "axes": [{"name": "A", "question": "q2"}]})
        self.assertFalse(is_error, text)
        ctx = store.find_context(store.load(), "legacy2")
        self.assertEqual([a["name"] for a in ctx["axes"]], ["A"])
        self.assertEqual(ctx["axes"][0]["question"], "q2")
        self.assertEqual(ctx["axes"][0]["sublenses"], ["s"])

    def test_anonymous_axes_are_preserved_by_dedup(self):
        # Review finding (2026-07-25, round 6): two axes with an empty name in
        # a hand-edited store were both indexed under the same key by the
        # legacy dedup, destroying all but the first. Anonymous axes are not
        # identities: they must survive any learn untouched.
        self._write_store(lambda st: st["contexts"].append(
            {"name": "anon", "description": "d", "keywords": [], "traps": [],
             "axes": [{"name": "", "question": "q1", "sublenses": []},
                      {"name": "", "question": "q2", "sublenses": []}]}))
        text, is_error = server.call_tool(store.load(), "multidim_learn",
                                          {"context": "anon", "keywords": ["k"]})
        self.assertFalse(is_error, text)
        ctx = store.find_context(store.load(), "anon")
        self.assertEqual([a["question"] for a in ctx["axes"]], ["q1", "q2"])

    def test_created_axis_carries_full_shape(self):
        # Defaults are filled at creation: a stored axis always has question
        # and sublenses even when the learn call omitted them.
        server.call_tool(store.load(), "multidim_learn",
                         {"context": "c", "axes": [{"name": "Bare"}]})
        ctx = store.find_context(store.load(), "c")
        self.assertEqual(ctx["axes"][0], {"name": "Bare", "question": "",
                                          "sublenses": []})

    def test_distinct_axes_are_kept(self):
        server.call_tool(store.load(), "multidim_learn",
                         {"context": "c", "axes": [{"name": "A", "question": "qa"},
                                                   {"name": "B", "question": "qb"}]})
        ctx = store.find_context(store.load(), "c")
        self.assertEqual([a["name"] for a in ctx["axes"]], ["A", "B"])

    def test_creation_message_counts_stored_axes_not_sent(self):
        # Review finding (2026-07-25, round 4): two same-name axes in ONE
        # create call collapse to one; the message must report the stored
        # count, not "created with 2 axes".
        text, is_error = server.call_tool(store.load(), "multidim_learn",
                                          {"context": "c2",
                                           "axes": [{"name": "A", "question": "q1"},
                                                    {"name": "A", "question": "q2"}]})
        self.assertFalse(is_error, text)
        # exact up to the period: "created with 11 axes." must NOT match
        self.assertIn("created with 1 axes.", text)
        ctx = store.find_context(store.load(), "c2")
        self.assertEqual(len(ctx["axes"]), 1)


class TestD4GenericKeywords(RobustnessTestBase):
    def test_keywords_only_on_generic_is_an_actionable_error(self):
        # generic is never keyword-matched: storing keywords there is dead
        # data, so a keywords-only learn must fail loudly, not "succeed".
        text, is_error = server.call_tool(store.load(), "multidim_learn",
                                          {"context": "generic",
                                           "keywords": ["motrarissime"]})
        self.assertTrue(is_error)
        self.assertIn("generic", text)
        fresh = store.load()
        self.assertNotIn("motrarissime", store.find_context(fresh, "generic")["keywords"])

    def test_keywords_plus_invalid_trap_on_generic_is_still_an_error(self):
        # Review finding (2026-07-25, round 9): keywords + traps=[{}] slipped
        # past the keywords-only gate as "mixed", the invalid trap was then
        # refused by upsert_traps, and the call reported success while
        # learning NOTHING. Only a valid trap makes the call mixed.
        text, is_error = server.call_tool(store.load(), "multidim_learn",
                                          {"context": "generic",
                                           "keywords": ["mort"], "traps": [{}]})
        self.assertTrue(is_error)
        self.assertIn("generic", text)
        fresh = store.load()
        self.assertNotIn("mort", store.find_context(fresh, "generic")["keywords"])

    def test_keywords_plus_collision_refused_trap_on_generic_is_an_error(self):
        # Review finding (2026-07-25, round 10): a trap VALID in shape can
        # still be refused by upsert_traps (id/statement collision). If the
        # keywords were dropped and every trap failed, the call learned
        # nothing: the decision must fall AFTER upsert, under the lock.
        base = {"mandatory_question": "q?", "triggers": ["zz"]}
        server.call_tool(store.load(), "multidim_learn",
                         {"context": "generic",
                          "traps": [dict(base, trap_id="id1", statement="lesson one"),
                                    dict(base, trap_id="id2", statement="lesson two")]})
        text, is_error = server.call_tool(store.load(), "multidim_learn",
                                          {"context": "generic",
                                           "keywords": ["mort"],
                                           "traps": [dict(base, trap_id="id1",
                                                          statement="lesson two")]})
        self.assertTrue(is_error)
        self.assertIn("nothing learned", text)
        fresh = store.load()
        generic = store.find_context(fresh, "generic")
        self.assertNotIn("mort", generic["keywords"])
        stmts = sorted(t["statement"] for t in generic["traps"])
        self.assertEqual(stmts, ["lesson one", "lesson two"])

    def test_keyword_plus_identical_trap_resend_on_generic_is_a_noop_success(self):
        # Review finding (2026-07-25, round 11): re-sending an IDENTICAL trap
        # is a legitimate idempotent no-op (0 added, 0 updated, 0 refused),
        # not a failure: with a dropped keyword it must stay a success with
        # the ignored-keywords note, not flip to isError.
        trap = {"trap_id": "tid", "statement": "same lesson",
                "mandatory_question": "q?", "triggers": ["zz"]}
        server.call_tool(store.load(), "multidim_learn",
                         {"context": "generic", "traps": [trap]})
        text, is_error = server.call_tool(store.load(), "multidim_learn",
                                          {"context": "generic",
                                           "keywords": ["mort"], "traps": [trap]})
        self.assertFalse(is_error, text)
        self.assertIn("ignored", text)
        fresh = store.load()
        generic = store.find_context(fresh, "generic")
        self.assertNotIn("mort", generic["keywords"])
        self.assertEqual(len([t for t in generic["traps"]
                              if t["trap_id"] == "tid"]), 1)

    def test_mixed_learn_on_generic_succeeds_with_note_and_drops_keywords(self):
        # Axes on generic are legitimate; only the keywords are dead. The call
        # succeeds (scripted callers keep working) but says what was ignored.
        text, is_error = server.call_tool(store.load(), "multidim_learn",
                                          {"context": "generic",
                                           "keywords": ["motrarissime"],
                                           "axes": [{"name": "AxeG", "question": "q"}]})
        self.assertFalse(is_error, text)
        self.assertIn("ignored", text)
        fresh = store.load()
        generic = store.find_context(fresh, "generic")
        self.assertNotIn("motrarissime", generic["keywords"])
        self.assertIn("AxeG", [a["name"] for a in generic["axes"]])


class TestFastPathTrapMigration(RobustnessTestBase):
    def test_trap_without_active_is_migrated_despite_fast_path(self):
        # Review finding (2026-07-25, round 5): a FULLY migrated store plus a
        # valid trap lacking 'active' slipped through the lock-free fast path
        # ("traps" key present), the active=True migration never ran, and
        # select_traps -- which requires active is True -- silently never
        # injected the lesson. The fast path must defer to the locked
        # migration whenever any trap misses 'active'.
        from phases_oss.multidim import frames
        trap = {"trap_id": "t1", "statement": "lesson", "mandatory_question": "asked?",
                "triggers": ["zorglubxyz"]}  # no 'active'
        self._write_store(lambda st: st["contexts"].append(
            {"name": "tctx", "description": "d", "keywords": [], "axes": [],
             "traps": [trap]}))
        st = store.load()
        loaded = store.find_context(st, "tctx")["traps"][0]
        self.assertIs(loaded.get("active"), True)
        selected = frames.select_traps(store.find_context(st, "tctx"),
                                       "sujet zorglubxyz")
        self.assertEqual([t["trap_id"] for t in selected], ["t1"])


class TestD5PrivateKeysNeverPersisted(RobustnessTestBase):
    def test_reset_markers_do_not_leak_to_disk_through_mutate(self):
        path = store.paths.store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json", encoding="utf-8")
        # learn right after the corrupt-store reset: mutate() persists the
        # reset store; the in-memory markers must not follow it to disk
        text, is_error = server.call_tool({"version": 1, "contexts": []},
                                          "multidim_learn",
                                          {"context": "c1", "keywords": ["k"]})
        self.assertFalse(is_error, text)
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        leaked = [k for k in on_disk if k in store.PRIVATE_MARKERS]
        self.assertEqual(leaked, [])

    def test_markers_persisted_by_prefix_version_are_scrubbed_on_load(self):
        # Review finding (2026-07-25, round 8): markers written to disk by a
        # PRE-FIX save() passed the fast path forever. load() must fall to the
        # locked path, scrub them from memory AND re-write the clean copy.
        path = self._write_store(lambda st: st.update(
            {"_reset_reason": "old", "_backup": "C:/somewhere/store.json.bak"}))
        st = store.load()
        self.assertNotIn("_reset_reason", st)
        self.assertNotIn("_backup", st)
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("_reset_reason", on_disk)
        self.assertNotIn("_backup", on_disk)

    def test_unknown_underscore_key_is_not_silently_dropped(self):
        # Review finding (2026-07-25, round 2): only the KNOWN internal
        # markers are filtered; a caller extension key must survive save().
        st = store.load()
        st["_vendor_extension"] = {"kept": True}
        store.save(st)
        on_disk = json.loads(store.paths.store_path().read_text(encoding="utf-8"))
        self.assertEqual(on_disk.get("_vendor_extension"), {"kept": True})
        self.assertNotIn("_reset_reason", on_disk)
        self.assertNotIn("_backup", on_disk)

    def test_reset_markers_still_reported_in_memory(self):
        # The markers stay useful to the caller of load(): only the DISK copy
        # must be clean.
        path = store.paths.store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json", encoding="utf-8")
        st = store.load()
        self.assertIn("_reset_reason", st)
        self.assertIn("_backup", st)
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("_reset_reason", on_disk)
        self.assertNotIn("_backup", on_disk)


class TestD6ReplaceRetry(RobustnessTestBase):
    def test_save_survives_transient_permission_error(self):
        # D6 (lot 2): a transient Windows sharing violation (reader holding
        # the file for milliseconds) must be absorbed by the bounded retry.
        real_replace = os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] <= 3:
                raise PermissionError(13, "sharing violation")
            return real_replace(src, dst)

        st = store.load()
        with mock.patch.object(store, "REPLACE_BACKOFF_S", 0.001), \
                mock.patch.object(store.os, "replace", side_effect=flaky):
            store.save(st)
        self.assertGreaterEqual(calls["n"], 4)
        on_disk = json.loads(store.paths.store_path().read_text(encoding="utf-8"))
        self.assertIn("contexts", on_disk)

    def test_save_fails_closed_after_persistent_permission_error(self):
        # a PERSISTENT hold still fails with the original error after the
        # last attempt, and the temp file never lingers next to the store
        st = store.load()
        with mock.patch.object(store, "REPLACE_BACKOFF_S", 0.001), \
                mock.patch.object(store.os, "replace",
                                  side_effect=PermissionError(13, "denied")):
            with self.assertRaises(PermissionError):
                store.save(st)
        leftovers = [p.name for p in store.paths.store_path().parent.iterdir()
                     if p.name.startswith("store-")]
        self.assertEqual(leftovers, [])

    @unittest.skipUnless(sys.platform.startswith("win"),
                         "Windows file-sharing semantics")
    def test_save_succeeds_while_reader_briefly_holds_the_file(self):
        # real integration: a reader holds the store open ~0.15 s (far below
        # the ~0.9 s retry budget); save() must win once the handle closes.
        # This exact scenario failed with PermissionError before the fix.
        st = store.load()
        path = store.paths.store_path()
        started = threading.Event()

        def hold():
            with open(path, "r", encoding="utf-8") as fh:
                fh.read(5)
                started.set()
                time.sleep(0.15)

        t = threading.Thread(target=hold)
        t.start()
        self.assertTrue(started.wait(timeout=2.0))
        try:
            store.save(st)  # must retry through the reader's hold
        finally:
            t.join()
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("contexts", on_disk)


class TestKimiFindings(RobustnessTestBase):
    def test_markers_survive_learn_in_memory(self):
        # Kimi finding 1 (2026-07-27): the reset markers are the caller's
        # diagnostics; the post-learn in-place refresh must not discard them
        # (the disk copy alone stays filtered).
        path = store.paths.store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json", encoding="utf-8")
        st = store.load()
        self.assertIn("_reset_reason", st)
        text, is_error = server.call_tool(st, "multidim_learn",
                                          {"context": "c1", "keywords": ["k"]})
        self.assertFalse(is_error, text)
        self.assertIn("_reset_reason", st)
        self.assertIn("_backup", st)
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("_reset_reason", on_disk)

    def test_markers_survive_handle_message_reload(self):
        # Review finding (2026-07-27, lot 2 round 1): the per-call reload in
        # handle_message wiped the markers exactly like the learn path did --
        # the same contract applies to every in-place refresh.
        path = store.paths.store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json", encoding="utf-8")
        st = store.load()
        self.assertIn("_reset_reason", st)
        resp = server.handle_message(st, {"jsonrpc": "2.0", "id": 1,
                                          "method": "tools/call",
                                          "params": {"name": "multidim_contexts",
                                                     "arguments": {}}})
        self.assertNotIn("error", resp)
        self.assertIn("_reset_reason", st)
        self.assertIn("_backup", st)

    def test_fresh_markers_from_reload_win_over_old_ones(self):
        # Review finding (2026-07-27, lot 2 round 2): if the per-call reload
        # itself resets a corrupt store, ITS markers are newer than the ones
        # carried in memory -- the old ones must not overwrite them.
        st = store.load()
        st["_reset_reason"] = "OLD_MARKER"
        path = store.paths.store_path()
        path.write_text("{ corrupt again", encoding="utf-8")
        resp = server.handle_message(st, {"jsonrpc": "2.0", "id": 1,
                                          "method": "tools/call",
                                          "params": {"name": "multidim_contexts",
                                                     "arguments": {}}})
        self.assertNotIn("error", resp)
        self.assertIn("_reset_reason", st)
        self.assertNotEqual(st["_reset_reason"], "OLD_MARKER")

    def test_learn_message_reports_axis_counters(self):
        # Kimi finding 2: like traps, the message distinguishes an addition
        # from an in-place update.
        text, _ = server.call_tool(store.load(), "multidim_learn",
                                   {"context": "c", "axes": [{"name": "A", "question": "q1"}]})
        self.assertIn("created with 1 axes.", text)
        text, _ = server.call_tool(store.load(), "multidim_learn",
                                   {"context": "c", "axes": [{"name": "A", "question": "q2"},
                                                             {"name": "B", "question": "qb"}]})
        self.assertIn("Axes: 1 added, 1 updated.", text)

    def test_case_variant_generic_is_never_keyword_matched(self):
        # Kimi finding 3: a hand-edited 'Generic' must be treated as the
        # fallback family by DETECTION too, not keyword-matched while learn
        # treats it as generic (one predicate, one semantics).
        self._write_store(lambda st: st["contexts"].append(
            {"name": "Generic", "description": "d", "keywords": ["zorglubxyz"],
             "axes": [], "traps": []}))
        c, score = server.detect_context(store.load(), "sujet zorglubxyz")
        self.assertEqual(c.get("name"), "generic")
        self.assertEqual(score, 0)

    def test_case_variant_generic_learn_guard_applies(self):
        text, is_error = server.call_tool(store.load(), "multidim_learn",
                                          {"context": "GENERIC",
                                           "keywords": ["mortx"]})
        self.assertTrue(is_error)
        self.assertIn("generic", text.lower())


if __name__ == "__main__":
    unittest.main()
