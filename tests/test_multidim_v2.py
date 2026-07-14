"""Tests for the v2 contract of the bundled Multidim server.

Covers: the v2 frame (format="v2"), the deterministic stateless validator
(multidim_validate), learned traps (multidim_learn), the hardened context-name
rule, and the additive store migration (traps + generic_blacklist).
"""

import copy
import json
import os
import tempfile
import unittest

from phases_oss.multidim import base_contexts, frames, server, store
from phases_oss.multidim import paths


def _neutral_store():
    s = {"version": base_contexts.BASE_VERSION, "contexts": base_contexts.base_contexts()}
    store.migrate_additive(s)
    return s


class _IsolatedStoreCase(unittest.TestCase):
    """Isolate every store write to a temp dir (learn persists the store)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="phases-mdv2-")
        self._prev = os.environ.get("PHASES_OSS_HOME")
        os.environ["PHASES_OSS_HOME"] = self._tmp.name
        self.store = _neutral_store()

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("PHASES_OSS_HOME", None)
        else:
            os.environ["PHASES_OSS_HOME"] = self._prev
        self._tmp.cleanup()

    def analyze_v2(self, subject, context=None, depth="deep"):
        args = {"subject": subject, "depth": depth, "format": "v2"}
        if context:
            args["context"] = context
        text, is_error = server.call_tool(self.store, "multidim_analyze", args)
        self.assertFalse(is_error, text)
        return json.loads(text)

    def validate(self, frame, analysis):
        return server.call_tool(self.store, "multidim_validate",
                                {"frame": frame, "analysis": analysis})


def _golden_core_analysis():
    """Minimal analysis that must pass a core-depth frame."""
    return {
        "facts": [{"fact_id": "F1",
                   "statement": "the engine parses configuration before boot"}],
        "hypotheses": [{"hypothesis_id": "H1",
                        "statement": "startup latency comes from disk seeks"}],
        "alternatives": [{"alternative_id": "A1",
                          "statement": "network handshake dominates the delay"}],
        "cross_talk": {"tensions": ["speed versus durability trade"],
                       "blind_spots": ["no data from production hosts"]},
        "synthesis": {"statement": "disk seeks remain the prime suspect",
                      "references": ["F1", "H1"]},
    }


def _golden_deep_analysis():
    """Analysis that must pass a deep-depth frame (all 11 sections)."""
    return {
        "facts": [{"fact_id": "F1",
                   "statement": "the engine parses configuration before boot"}],
        "hypotheses": [{
            "hypothesis_id": "H1",
            "statement": "startup latency comes from disk seeks",
            "falsification_test": ("measure boot time on a ram disk; if the "
                                   "latency stays, the hypothesis falls"),
        }],
        "alternatives": [
            {"alternative_id": "A1",
             "statement": "network handshake dominates the delay"},
            {"alternative_id": "A2",
             "statement": "logging volume slows the first request"},
        ],
        "hidden_dependencies": [{"dependency_id": "D1",
                                 "dependency": "kernel page cache warmth"}],
        "second_order_risks": [{
            "risk_id": "R1",
            "risk": "cache regression after the fix",
            "first_order_effect": "queries slow down twice",
            "second_order_effect": "users abandon the dashboard entirely",
        }],
        "contradictions": [{"contradiction_id": "C1",
                            "contradiction": "profiler blames cpu while metrics blame io"}],
        "open_questions": [{"question_id": "Q1",
                            "question": "which storage tier serves the first read"}],
        "decision_criteria": [{"criterion_id": "DC1",
                               "criterion": "adopt the fix only when p99 drops under one second"}],
        "cross_talk": {"tensions": ["speed versus durability trade"],
                       "blind_spots": ["no data from production hosts"]},
        "premortem": {"story": ("six months later the project stalled because "
                                "nobody owned the benchmark suite")},
        "synthesis": {"statement": ("disk seeks remain the prime suspect pending "
                                    "the ram disk trial"),
                      "references": ["F1", "H1", "R1"]},
    }


class TestFrameV2(_IsolatedStoreCase):
    def test_frame_has_contract_fields(self):
        frame = self.analyze_v2("review this code change for regressions")
        for key in ("analysis_schema_version", "store_version", "context", "depth",
                    "subject", "axes", "mandatory_questions", "learned_traps",
                    "required_sections", "constraints", "validation_rules",
                    "max_validation_rounds", "generic_blacklist", "host_hints",
                    "frame_hash", "frame_id"):
            self.assertIn(key, frame)
        self.assertEqual(frame["analysis_schema_version"], 2)
        self.assertTrue(frame["frame_id"].startswith("frame_"))

    def test_frame_hash_is_deterministic(self):
        f1 = self.analyze_v2("review this code change", context="code_review")
        f2 = self.analyze_v2("review this code change", context="code_review")
        self.assertEqual(f1["frame_hash"], f2["frame_hash"])

    def test_depth_changes_required_sections(self):
        core = self.analyze_v2("a subject", context="generic", depth="core")
        deep = self.analyze_v2("a subject", context="generic", depth="deep")
        full = self.analyze_v2("a subject", context="generic", depth="full")
        self.assertEqual(len(core["required_sections"]), 5)
        self.assertIn("premortem", deep["required_sections"])
        self.assertIn("targeted_recursion", full["required_sections"])
        self.assertNotIn("targeted_recursion", deep["required_sections"])

    def test_forced_context_scores_minus_one(self):
        frame = self.analyze_v2("anything", context="decision")
        self.assertEqual(frame["context"]["score"], -1)
        self.assertIn("forced", frame["context"]["explanation"])

    def test_frame_carries_store_blacklist(self):
        frame = self.analyze_v2("a subject", context="generic")
        self.assertEqual(frame["generic_blacklist"], self.store["generic_blacklist"])
        self.assertTrue(frame["generic_blacklist"])

    def test_text_grid_keeps_forced_score_zero(self):
        # backward compatibility: the legacy text grid shows score 0 for a
        # forced context, exactly as before the v2 port
        text, is_error = server.call_tool(
            self.store, "multidim_analyze",
            {"subject": "anything", "context": "decision"})
        self.assertFalse(is_error, text)
        self.assertIn("(detection score 0)", text)

    def test_frame_publishes_section_schemas(self):
        # the frame must ADVERTISE the structural shapes the validator enforces
        for depth in ("core", "deep", "full"):
            frame = self.analyze_v2("a subject", context="generic", depth=depth)
            self.assertIn("section_schemas", frame)
            for section in frame["required_sections"]:
                self.assertIn(section, frame["section_schemas"],
                              "unadvertised section at depth {}: {}".format(depth, section))
        deep = self.analyze_v2("a subject", context="generic", depth="deep")
        self.assertEqual(deep["section_schemas"]["facts"]["item_required_fields"],
                         ["fact_id", "statement"])
        self.assertIn("falsification_test", deep["section_schemas"]["hypotheses"]["note"])
        self.assertIn("first_order_effect",
                      deep["section_schemas"]["second_order_risks"]["item_required_fields"])

    def test_invalid_format_is_error(self):
        text, is_error = server.call_tool(
            self.store, "multidim_analyze", {"subject": "s", "format": "v3"})
        self.assertTrue(is_error)


class TestValidate(_IsolatedStoreCase):
    def test_golden_core_accepts(self):
        frame = self.analyze_v2("a plain subject", context="generic", depth="core")
        text, is_error = self.validate(frame, _golden_core_analysis())
        self.assertFalse(is_error, text)
        verdict = json.loads(text)
        self.assertEqual(verdict["verdict"], "ACCEPT", text)
        self.assertEqual(verdict["frame_hash"], frame["frame_hash"])

    def test_golden_deep_accepts(self):
        frame = self.analyze_v2("a plain subject", context="generic", depth="deep")
        text, is_error = self.validate(frame, _golden_deep_analysis())
        self.assertFalse(is_error, text)
        self.assertEqual(json.loads(text)["verdict"], "ACCEPT", text)

    def test_golden_full_accepts_with_object_sub_analysis(self):
        frame = self.analyze_v2("a plain subject", context="generic", depth="full")
        analysis = _golden_deep_analysis()
        analysis["targeted_recursion"] = {
            "focus": "the missing production data blind spot",
            "sub_analysis": {"finding": "no production host ever fed the benchmark",
                             "consequence": "every conclusion is lab-only"},
        }
        text, is_error = self.validate(frame, analysis)
        self.assertFalse(is_error, text)
        self.assertEqual(json.loads(text)["verdict"], "ACCEPT", text)

    def test_validate_never_writes_the_store(self):
        frame = self.analyze_v2("a plain subject", context="generic", depth="core")
        before = json.dumps(self.store, sort_keys=True)
        self.validate(frame, _golden_core_analysis())
        self.assertEqual(before, json.dumps(self.store, sort_keys=True))

    def test_missing_section_rejects(self):
        frame = self.analyze_v2("a plain subject", context="generic", depth="core")
        analysis = _golden_core_analysis()
        del analysis["alternatives"]
        verdict = json.loads(self.validate(frame, analysis)[0])
        self.assertEqual(verdict["verdict"], "REJECT")
        codes = {c for r in verdict["section_results"] for c in r["error_codes"]}
        self.assertIn("SECTION_MISSING", codes)

    def test_item_without_id_rejects(self):
        frame = self.analyze_v2("a plain subject", context="generic", depth="core")
        analysis = _golden_core_analysis()
        analysis["facts"] = [{"statement": "an anonymous fact"}]
        verdict = json.loads(self.validate(frame, analysis)[0])
        codes = {c for r in verdict["section_results"] for c in r["error_codes"]}
        self.assertIn("ITEM_FIELDS_MISSING", codes)

    def test_unfalsifiable_primary_rejects_at_deep(self):
        frame = self.analyze_v2("a plain subject", context="generic", depth="deep")
        analysis = _golden_deep_analysis()
        del analysis["hypotheses"][0]["falsification_test"]
        verdict = json.loads(self.validate(frame, analysis)[0])
        codes = {c for r in verdict["section_results"] for c in r["error_codes"]}
        self.assertIn("HYPOTHESIS_NOT_FALSIFIABLE", codes)

    def test_alternative_duplicating_primary_rejects(self):
        frame = self.analyze_v2("a plain subject", context="generic", depth="core")
        analysis = _golden_core_analysis()
        analysis["alternatives"] = [{
            "alternative_id": "A1",
            "statement": analysis["hypotheses"][0]["statement"],
        }]
        verdict = json.loads(self.validate(frame, analysis)[0])
        codes = {c for r in verdict["section_results"] for c in r["error_codes"]}
        self.assertIn("ALTERNATIVE_DUPLICATES_PRIMARY", codes)

    def test_duplicate_detection_works_on_non_latin_text(self):
        # CJK text used to produce zero tokens (ascii-only tokenizer), making
        # the duplicate check blind: identical H1/A1 got an ACCEPT
        frame = self.analyze_v2("a plain subject", context="generic", depth="core")
        analysis = _golden_core_analysis()
        # real CJK built from code points so the SOURCE stays pure ASCII (a raw
        # literal could be mojibaked in transit and turn the test into a no-op):
        # four Han ideographs in the CJK Unified block
        cjk = "".join(chr(cp) for cp in (0x7F13, 0x5B58, 0x672A, 0x547D))
        self.assertTrue(all(ord(ch) >= 0x4E00 for ch in cjk))
        analysis["hypotheses"] = [{"hypothesis_id": "H1", "statement": cjk}]
        analysis["alternatives"] = [{"alternative_id": "A1", "statement": cjk}]
        verdict = json.loads(self.validate(frame, analysis)[0])
        self.assertEqual(verdict["verdict"], "REJECT")
        codes = {c for r in verdict["section_results"] for c in r["error_codes"]}
        self.assertIn("ALTERNATIVE_DUPLICATES_PRIMARY", codes)

    def test_second_order_repeating_first_rejects(self):
        frame = self.analyze_v2("a plain subject", context="generic", depth="deep")
        analysis = _golden_deep_analysis()
        analysis["second_order_risks"][0]["second_order_effect"] = (
            analysis["second_order_risks"][0]["first_order_effect"])
        verdict = json.loads(self.validate(frame, analysis)[0])
        codes = {c for r in verdict["section_results"] for c in r["error_codes"]}
        self.assertIn("SECOND_ORDER_REPEATS_FIRST", codes)

    def test_premortem_copy_with_padding_still_rejects(self):
        frame = self.analyze_v2("a plain subject", context="generic", depth="deep")
        analysis = _golden_deep_analysis()
        # story copies R1.risk verbatim; a distinct padded field dilutes the
        # flattened text -- the field-by-field comparison must still catch it
        analysis["premortem"] = {
            "story": analysis["second_order_risks"][0]["risk"],
            "trigger_conditions": ["an unrelated long padding text about the "
                                   "weather and the seasons of the year"],
        }
        verdict = json.loads(self.validate(frame, analysis)[0])
        self.assertEqual(verdict["verdict"], "REJECT")
        codes = {c for r in verdict["section_results"] for c in r["error_codes"]}
        self.assertIn("PREMORTEM_COPIES_RISKS", codes)

    def test_synthesis_with_unknown_reference_rejects(self):
        frame = self.analyze_v2("a plain subject", context="generic", depth="core")
        analysis = _golden_core_analysis()
        analysis["synthesis"]["references"] = ["F1", "GHOST9"]
        verdict = json.loads(self.validate(frame, analysis)[0])
        codes = {c for r in verdict["section_results"] for c in r["error_codes"]}
        self.assertIn("SYNTHESIS_REFERENCES_UNKNOWN_ID", codes)

    def test_hollow_section_rejects_on_density(self):
        frame = self.analyze_v2("a plain subject", context="generic", depth="core")
        analysis = _golden_core_analysis()
        analysis["facts"] = [{
            "fact_id": "F1",
            "statement": "It depends on the context. We must remain vigilant.",
        }]
        # keep the synthesis grounded on a still-existing id
        analysis["synthesis"]["references"] = ["F1", "H1"]
        verdict = json.loads(self.validate(frame, analysis)[0])
        codes = {c for r in verdict["section_results"] for c in r["error_codes"]}
        self.assertIn("GENERIC_DENSITY_HIGH", codes)

    def test_too_many_hypotheses_rejects_at_core(self):
        frame = self.analyze_v2("a plain subject", context="generic", depth="core")
        analysis = _golden_core_analysis()
        analysis["hypotheses"] = [
            {"hypothesis_id": "H{}".format(i),
             "statement": "hypothesis number {} stands alone".format(i)}
            for i in range(1, 4)  # 3 > core max of 2
        ]
        verdict = json.loads(self.validate(frame, analysis)[0])
        codes = {c for r in verdict["section_results"] for c in r["error_codes"]}
        self.assertIn("TOO_MANY_HYPOTHESES", codes)

    def test_tampered_hash_is_refused(self):
        frame = self.analyze_v2("a plain subject", context="generic", depth="core")
        frame["frame_hash"] = "0" * 64
        text, is_error = self.validate(frame, _golden_core_analysis())
        self.assertTrue(is_error, text)

    def test_stripped_contract_is_neutralized_by_rebuild(self):
        # stripping required_sections does NOT weaken the check: the validator
        # runs against the frame REBUILT from the store, so the full contract
        # still applies and the thin analysis is rejected.
        frame = self.analyze_v2("a plain subject", context="generic", depth="core")
        tampered = copy.deepcopy(frame)
        tampered["required_sections"] = ["facts"]
        thin = {"facts": _golden_core_analysis()["facts"]}
        text, is_error = self.validate(tampered, thin)
        self.assertFalse(is_error, text)
        self.assertEqual(json.loads(text)["verdict"], "REJECT")

    def test_wrong_schema_version_is_refused(self):
        frame = self.analyze_v2("a plain subject", context="generic", depth="core")
        frame["analysis_schema_version"] = 1
        text, is_error = self.validate(frame, _golden_core_analysis())
        self.assertTrue(is_error, text)

    def test_missing_params_are_refused(self):
        text, is_error = server.call_tool(self.store, "multidim_validate", {})
        self.assertTrue(is_error, text)
        text, is_error = server.call_tool(self.store, "multidim_validate",
                                          {"frame": {}})
        self.assertTrue(is_error, text)


class TestLearnV2(_IsolatedStoreCase):
    def test_sentence_context_name_is_refused(self):
        text, is_error = server.call_tool(self.store, "multidim_learn", {
            "context": "Emerges from arbitrating five proposals about caching."})
        self.assertTrue(is_error)
        self.assertIn("refused", text)

    def test_short_identifier_is_accepted(self):
        text, is_error = server.call_tool(self.store, "multidim_learn",
                                          {"context": "incident_review"})
        self.assertFalse(is_error, text)
        self.assertIsNotNone(store.find_context(self.store, "incident_review"))

    def test_failed_save_leaves_store_unchanged(self):
        # if persistence fails, learn must not leave a ghost context in memory
        import unittest.mock as mock
        before = json.dumps(self.store, sort_keys=True)
        with mock.patch("phases_oss.multidim.store.save",
                        side_effect=OSError("disk full")):
            text, is_error = server.call_tool(
                self.store, "multidim_learn", {"context": "brand_new_ctx"})
        self.assertTrue(is_error, text)
        # in-memory store is byte-identical to before: no ghost context persisted
        self.assertEqual(json.dumps(self.store, sort_keys=True), before)
        self.assertIsNone(store.find_context(self.store, "brand_new_ctx"))

    def test_existing_context_stays_enrichable_with_spaces(self):
        n_before = len(self.store["contexts"])
        text, is_error = server.call_tool(self.store, "multidim_learn", {
            "context": "  code_review  ", "keywords": ["hotfix"]})
        self.assertFalse(is_error, text)
        self.assertEqual(len(self.store["contexts"]), n_before)  # no duplicate
        self.assertIn("hotfix", store.find_context(self.store, "code_review")["keywords"])

    def test_trap_is_learned_and_injected(self):
        trap = {"statement": "a rule picked on the sample that validates it is unproven",
                "mandatory_question": "was the rule chosen on the same sample that validates it?",
                "triggers": ["backtest"], "severity": "high"}
        text, is_error = server.call_tool(self.store, "multidim_learn", {
            "context": "decision", "traps": [trap]})
        self.assertFalse(is_error, text)
        self.assertIn("Traps: 1 added", text)
        frame = self.analyze_v2("choose a backtest window", context="decision")
        self.assertEqual(len(frame["learned_traps"]), 1)
        self.assertIn(trap["mandatory_question"], frame["mandatory_questions"])
        # no trigger match -> not injected
        frame2 = self.analyze_v2("pick a holiday destination", context="decision")
        self.assertEqual(frame2["learned_traps"], [])

    def test_reversed_statements_are_two_distinct_traps(self):
        # same words, opposite meaning: the dedup key must keep token order
        t1 = {"statement": "client pays supplier",
              "mandatory_question": "who invoices whom?", "triggers": ["billing"]}
        t2 = {"statement": "supplier pays client",
              "mandatory_question": "who refunds whom?", "triggers": ["billing"]}
        text1, err1 = server.call_tool(self.store, "multidim_learn",
                                       {"context": "decision", "traps": [t1]})
        text2, err2 = server.call_tool(self.store, "multidim_learn",
                                       {"context": "decision", "traps": [t2]})
        self.assertFalse(err1, text1)
        self.assertFalse(err2, text2)
        self.assertIn("Traps: 1 added", text2)   # added, not updated
        ctx = store.find_context(self.store, "decision")
        statements = {t["statement"] for t in ctx["traps"]}
        self.assertEqual(statements, {"client pays supplier", "supplier pays client"})

    def test_trap_question_appears_in_mandatory_questions(self):
        # parity with the canonical engine: a triggered trap injects its
        # question into mandatory_questions, but the validator does NOT force
        # an answer -- the sections are trusted to embody the analysis
        trap = {"statement": "unbounded retries hide outages",
                "mandatory_question": "what is the retry budget and who enforces it?",
                "triggers": ["subject"]}
        server.call_tool(self.store, "multidim_learn", {"context": "generic", "traps": [trap]})
        frame = self.analyze_v2("a plain subject", context="generic", depth="core")
        self.assertIn(trap["mandatory_question"], frame["mandatory_questions"])
        # golden analysis with no explicit trap answer still ACCEPTs (parity)
        verdict = json.loads(self.validate(frame, _golden_core_analysis())[0])
        self.assertEqual(verdict["verdict"], "ACCEPT", verdict)

    def test_trap_dedup_updates_not_duplicates(self):
        trap = {"statement": "the same lesson twice",
                "mandatory_question": "asked once?", "triggers": ["dup"]}
        server.call_tool(self.store, "multidim_learn", {"context": "decision", "traps": [trap]})
        trap2 = dict(trap, severity="high")
        text, is_error = server.call_tool(self.store, "multidim_learn",
                                          {"context": "decision", "traps": [trap2]})
        self.assertFalse(is_error, text)
        ctx = store.find_context(self.store, "decision")
        self.assertEqual(len(ctx["traps"]), 1)
        self.assertEqual(ctx["traps"][0]["severity"], "high")

    def test_resend_without_id_preserves_stable_id(self):
        t1 = {"trap_id": "stable-id", "statement": "same lesson",
              "mandatory_question": "asked?", "triggers": ["dup"]}
        server.call_tool(self.store, "multidim_learn", {"context": "decision", "traps": [t1]})
        t2 = {"statement": "same lesson", "mandatory_question": "asked?",
              "triggers": ["dup"], "severity": "high"}   # no trap_id
        text, is_error = server.call_tool(self.store, "multidim_learn",
                                          {"context": "decision", "traps": [t2]})
        self.assertFalse(is_error, text)
        ctx = store.find_context(self.store, "decision")
        self.assertEqual(len(ctx["traps"]), 1)
        self.assertEqual(ctx["traps"][0]["trap_id"], "stable-id")
        self.assertEqual(ctx["traps"][0]["severity"], "high")

    def test_inactive_trap_is_never_injected(self):
        trap = {"statement": "a disabled lesson",
                "mandatory_question": "still asked?", "triggers": ["disabled"],
                "active": False}
        server.call_tool(self.store, "multidim_learn", {"context": "decision", "traps": [trap]})
        frame = self.analyze_v2("a disabled trigger word", context="decision")
        self.assertEqual(frame["learned_traps"], [])

    def test_explicit_id_is_stripped_no_ghost_duplicate(self):
        t1 = {"trap_id": "id", "statement": "first wording",
              "mandatory_question": "q1?", "triggers": ["w"]}
        t2 = {"trap_id": " id ", "statement": "second wording",
              "mandatory_question": "q2?", "triggers": ["w"]}
        server.call_tool(self.store, "multidim_learn", {"context": "decision", "traps": [t1]})
        server.call_tool(self.store, "multidim_learn", {"context": "decision", "traps": [t2]})
        ctx = store.find_context(self.store, "decision")
        # ' id ' strips to 'id': same lesson updated, never a ghost duplicate
        self.assertEqual(len(ctx["traps"]), 1)
        self.assertEqual(ctx["traps"][0]["trap_id"], "id")

    def test_padded_trap_id_on_disk_is_malformed(self):
        p = paths.store_path()
        bad = {"version": base_contexts.BASE_VERSION,
               "contexts": base_contexts.base_contexts()}
        bad["contexts"][0]["traps"] = [{"trap_id": " padded ", "statement": "s",
                                        "mandatory_question": "q",
                                        "triggers": ["w"], "active": True}]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(bad), encoding="utf-8")
        loaded = store.load()
        self.assertTrue(p.with_name(p.name + ".bak").exists())
        self.assertIn("generic", store.list_context_names(loaded))

    def test_derived_id_collision_refuses_never_merges(self):
        incoming = {"statement": "brand new lesson about retries",
                    "mandatory_question": "asked?", "triggers": ["retry"]}
        derived_id = frames.sanitize_trap("decision", incoming)[0]["trap_id"]
        ctx = store.find_context(self.store, "decision")
        # forge an existing trap holding the SAME id but a DIFFERENT lesson
        ctx["traps"] = [{"trap_id": derived_id,
                         "statement": "a totally different lesson",
                         "mandatory_question": "other?", "triggers": ["other"],
                         "severity": "low", "source": "explicit_learning",
                         "active": True, "schema_version": 1}]
        added, updated, errors = frames.upsert_traps(ctx, [incoming])
        self.assertEqual((added, updated), (0, 0))
        self.assertTrue(errors and "collision" in errors[0])
        # the pre-existing lesson survived untouched
        self.assertEqual(ctx["traps"][0]["statement"], "a totally different lesson")

    def test_malformed_trap_is_reported(self):
        text, is_error = server.call_tool(self.store, "multidim_learn", {
            "context": "decision", "traps": [{"statement": "no question or trigger"}]})
        self.assertFalse(is_error, text)   # the learn succeeds, the trap is refused
        self.assertIn("Traps refused", text)
        self.assertEqual(store.find_context(self.store, "decision").get("traps"), [])


class TestStoreMigration(_IsolatedStoreCase):
    def test_pre_v2_store_gains_fields_without_reset(self):
        # write a valid store WITHOUT traps/generic_blacklist (pre-v2 on-disk shape)
        p = paths.store_path()
        legacy = {"version": base_contexts.BASE_VERSION,
                  "contexts": base_contexts.base_contexts()}
        legacy["contexts"].append({"name": "mine", "description": "kept",
                                   "keywords": [], "axes": []})
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(legacy), encoding="utf-8")
        loaded = store.load()
        # no reset, no backup, no version bump; fields completed and persisted
        self.assertFalse(p.with_name(p.name + ".bak").exists())
        self.assertEqual(loaded["version"], base_contexts.BASE_VERSION)
        self.assertIn("mine", store.list_context_names(loaded))
        self.assertEqual(loaded["generic_blacklist"], store.DEFAULT_GENERIC_BLACKLIST)
        for c in loaded["contexts"]:
            self.assertIn("traps", c)
        on_disk = json.loads(p.read_text(encoding="utf-8"))
        self.assertIn("generic_blacklist", on_disk)

    def test_trap_with_scalar_triggers_is_malformed(self):
        p = paths.store_path()
        bad = {"version": base_contexts.BASE_VERSION,
               "contexts": base_contexts.base_contexts()}
        bad["contexts"][0]["traps"] = [{"statement": "s", "mandatory_question": "q",
                                        "triggers": 1, "active": True}]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(bad), encoding="utf-8")
        loaded = store.load()
        # the malformed trap is caught at load: backup + reset, no crash later
        self.assertTrue(p.with_name(p.name + ".bak").exists())
        self.assertIn("generic", store.list_context_names(loaded))

    def test_null_blacklist_is_malformed_and_reset(self):
        p = paths.store_path()
        bad = {"version": base_contexts.BASE_VERSION,
               "contexts": base_contexts.base_contexts(),
               "generic_blacklist": None}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(bad), encoding="utf-8")
        loaded = store.load()
        self.assertTrue(p.with_name(p.name + ".bak").exists())
        self.assertIsInstance(loaded["generic_blacklist"], list)
        # and frame building works right away on the reset store
        text, is_error = server.call_tool(
            loaded, "multidim_analyze",
            {"subject": "s", "context": "generic", "format": "v2"})
        self.assertFalse(is_error, text)

    def test_trap_without_active_defaults_to_active_on_load(self):
        # a persisted trap omitting 'active' must not be silently ignored by
        # select_traps: load migrates the missing field to True
        p = paths.store_path()
        s = {"version": base_contexts.BASE_VERSION,
             "contexts": base_contexts.base_contexts()}
        store.find_context(s, "generic")["traps"] = [{
            "trap_id": "t1", "statement": "a lesson", "mandatory_question": "q?",
            "triggers": ["marker"]}]   # no 'active' key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(s), encoding="utf-8")
        loaded = store.load()
        self.assertFalse(p.with_name(p.name + ".bak").exists())  # not malformed
        trap = store.find_context(loaded, "generic")["traps"][0]
        self.assertIs(trap["active"], True)
        # and it now injects into a matching frame
        text, is_error = server.call_tool(
            loaded, "multidim_analyze",
            {"subject": "a marker subject", "context": "generic", "format": "v2"})
        self.assertFalse(is_error, text)
        self.assertEqual(len(json.loads(text)["learned_traps"]), 1)

    def test_trap_without_id_on_disk_is_malformed(self):
        # a trap without trap_id would make TRAP_QUESTION_UNANSWERED
        # unsatisfiable: reject the store at load, back up, reset
        p = paths.store_path()
        bad = {"version": base_contexts.BASE_VERSION,
               "contexts": base_contexts.base_contexts()}
        bad["contexts"][0]["traps"] = [{"statement": "s", "mandatory_question": "q",
                                        "triggers": ["word"], "active": True}]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(bad), encoding="utf-8")
        loaded = store.load()
        self.assertTrue(p.with_name(p.name + ".bak").exists())
        self.assertIn("generic", store.list_context_names(loaded))

    def test_context_with_non_list_traps_is_malformed(self):
        p = paths.store_path()
        bad = {"version": base_contexts.BASE_VERSION,
               "contexts": base_contexts.base_contexts()}
        bad["contexts"][0]["traps"] = "oops"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(bad), encoding="utf-8")
        loaded = store.load()
        # malformed -> backed up then reset to neutral seeds
        self.assertTrue(p.with_name(p.name + ".bak").exists())
        self.assertIn("generic", store.list_context_names(loaded))


class TestFramesUnit(unittest.TestCase):
    def test_frame_hash_is_self_verifiable(self):
        # recomputing the hash on the COMPLETE frame reproduces the stored hash
        # (frame_id/frame_hash excluded from the basis)
        s = _neutral_store()
        ctx = store.find_context(s, "generic")
        frame = frames.build_frame(s, ctx, "a subject", 0, "deep")
        self.assertEqual(frames.frame_hash_of(frame), frame["frame_hash"])

    def test_fold_normalizes_decomposed_and_precomposed(self):
        # "precision" with a precomposed vs a decomposed accent must fold the
        # same, so both route to the same context
        precomposed = "précision"          # é as U+00E9
        decomposed = "précision"          # e + combining acute U+0301
        # rebuild both forms from code points so the two byte forms genuinely
        # differ regardless of how the source file was saved
        precomposed = "pr" + chr(0x00E9) + "cision"          # e-acute U+00E9
        decomposed = "pr" + "e" + chr(0x0301) + "cision"     # e + combining acute
        self.assertNotEqual(precomposed, decomposed)
        self.assertEqual(frames.fold(precomposed), frames.fold(decomposed))
        self.assertEqual(frames.fold(precomposed), "precision")

    def test_frame_hash_changes_with_content(self):
        s = _neutral_store()
        ctx = store.find_context(s, "generic")
        f1 = frames.build_frame(s, ctx, "subject one", 0, "core")
        f2 = frames.build_frame(s, ctx, "subject two", 0, "core")
        self.assertNotEqual(f1["frame_hash"], f2["frame_hash"])

    def test_trap_matches_defends_against_scalar_triggers(self):
        trap = {"statement": "s", "mandatory_question": "q",
                "triggers": 1, "active": True}
        # in-memory malformed trap: never matches, never raises
        self.assertFalse(frames.trap_matches(trap, "any subject", {"any", "subject"}))
        ctx = {"name": "x", "axes": [], "traps": [trap]}
        self.assertEqual(frames.select_traps(ctx, "any subject"), [])

    def test_frame_does_not_alias_store_inner_lists(self):
        s = _neutral_store()
        ctx = store.find_context(s, "generic")
        original_sublenses = list(ctx["axes"][0]["sublenses"])
        frame = frames.build_frame(s, ctx, "subject", 0, "core")
        # mutating the frame's nested lists must not reach back into the store
        frame["axes"][0]["sublenses"].append("injected")
        self.assertEqual(ctx["axes"][0]["sublenses"], original_sublenses)

    def test_depth_params_cover_all_depths(self):
        self.assertEqual(set(frames.DEPTH_PARAMS), {"core", "deep", "full"})
        for params in frames.DEPTH_PARAMS.values():
            self.assertIn("required_sections", params)
            self.assertIn("max_validation_rounds", params)


if __name__ == "__main__":
    unittest.main()
