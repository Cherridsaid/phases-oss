"""Deterministic, STATELESS validation of a filled v2 analysis.

Pure function: ``(frame, analysis) -> per-section verdicts``. It never receives
the store (a structural guarantee, not a convention), writes nothing, calls no
LLM, and never judges the truth of the content: only structure, internal
consistency and checkable requirements.

Strict on the objective, humble on the rest: manifest duplication = REJECT,
ambiguous similarity = WARNING.

Ported from the canonical Multidim v2 engine; pure standard library.
"""

from __future__ import annotations

from .frames import (DICT_SECTIONS, ITEM_REQUIRED, LIST_SECTIONS,
                     STRUCT_REQUIRED, fold, tokens_of)

# Lexical thresholds (named constants, tunable; Jaccard overlap on folded
# tokens). REJECT is reserved for normalised equality (``normalized_equal``);
# Jaccard only feeds the WARNING band (ambiguous similarity).
SIMILARITY_WARN = 0.5          # ambiguous similarity -> WARNING
GENERIC_DENSITY_REJECT = 0.5   # more than half the sentences are filler

VERDICT_ORDER = {"ACCEPT": 0, "WARNING": 1, "REJECT": 2}


def similarity(a, b) -> float:
    """Jaccard on folded tokens: 0.0 (disjoint) -> 1.0 (same tokens).
    Defensive: any non-text counts as the empty string (never an AttributeError)."""
    if not isinstance(a, str):
        a = ""
    if not isinstance(b, str):
        b = ""
    ta = tokens_of(fold(a))
    tb = tokens_of(fold(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta | tb))


def ordered_tokens(s):
    """ORDERED sequence of folded alphanumeric tokens (keeps order and
    repetitions, unlike ``tokens_of`` which returns a set)."""
    out = []
    cur = []
    for ch in fold(s):
        if ch.isalnum():   # Unicode-aware, same rule as tokens_of
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def normalized_equal(a, b) -> bool:
    """True when a and b are the SAME idea: same ordered, non-empty sequence of
    folded tokens. Reserved for the REJECT verdict. Order matters: mere word
    overlap is not enough ('client pays supplier' vs 'supplier pays client' =
    opposite meaning, same words), nor is a high Jaccard."""
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    ta = ordered_tokens(a)
    tb = ordered_tokens(b)
    return bool(ta) and ta == tb


def section_text(value) -> str:
    """Flatten a section (string, list, dict) into text for the checks."""
    parts = []

    def walk(v):
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)

    walk(value)
    # each leaf stays a distinct sentence: otherwise 3 filler phrases from a
    # list, joined by a space, count as ONE sentence and the filler density
    # slips under the threshold
    return ". ".join(parts)


# Non-semantic keys: identifiers and enum/scalar fields. Excluded from the
# filler-density measure, otherwise "F1" or "observed" inflate the denominator
# and a hollow section lands on WARNING instead of REJECT. ``supporting_signals``
# stays counted: it is real prose, excluding it would create a false REJECT.
NON_SEMANTIC_KEYS = {"type", "weight", "probability", "impact", "confidence",
                     "blocking", "severity", "active", "schema_version",
                     "references"}


def semantic_text(value) -> str:
    """Like ``section_text`` but skips identifiers (``*_id``) and non-semantic
    fields: keeps only the PROSE, for the filler-phrase density."""
    parts = []

    def walk(v):
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for k, x in v.items():
                if k.endswith("_id") or k in NON_SEMANTIC_KEYS:
                    continue
                walk(x)

    walk(value)
    return ". ".join(parts)


def section_is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def generic_density(text, blacklist):
    """``(hollow sentence count, sentence count)``. A sentence is hollow when it
    contains a blacklisted phrase (substring match on folded text)."""
    folded_black = [fold(b) for b in blacklist if isinstance(b, str) and b.strip()]
    if not folded_black:
        return 0, 0
    raw = [s.strip() for s in
           text.replace("!", ".").replace("?", ".").replace("\n", ".").split(".")]
    sentences = [s for s in raw if s]
    hollow = 0
    for s in sentences:
        fs = fold(s)
        if any(b in fs for b in folded_black):
            hollow += 1
    return hollow, len(sentences)


def validate_analysis(frame, analysis):
    """Pure validator core. Returns the contract verdict dict.
    Inputs are assumed already structurally checked (fail-closed in the tool
    handler): here ``frame`` and ``analysis`` are usable dicts."""
    required = frame.get("required_sections") or []
    constraints = frame.get("constraints") or {}
    # a non-dict rule (null, string) is skipped cleanly: never an
    # AttributeError escaping the tool handler
    rules = {r.get("rule"): r for r in (frame.get("validation_rules") or [])
             if isinstance(r, dict)}
    blacklist = frame.get("generic_blacklist") or []

    results = {}  # section -> {"verdict","error_codes","details"}

    def report(section, verdict, code, detail):
        r = results.setdefault(section, {"section": section, "verdict": "ACCEPT",
                                         "error_codes": [], "details": []})
        if VERDICT_ORDER[verdict] > VERDICT_ORDER[r["verdict"]]:
            r["verdict"] = verdict
        if code and code not in r["error_codes"]:
            r["error_codes"].append(code)
        if detail:
            r["details"].append(detail)

    # 1. required sections present, non-empty, of the expected STRUCTURAL type.
    # Without this, filling every section with "x" earned an ACCEPT.
    # The structural constants (LIST_SECTIONS, DICT_SECTIONS, STRUCT_REQUIRED,
    # ITEM_REQUIRED) are the SAME objects frames.py publishes in the frame's
    # section_schemas: the published contract and the enforced contract cannot
    # drift apart.

    def field_filled(v):
        # TEXTUAL substance required somewhere: a list of nulls or a dict of
        # empty strings is not a filled field, but a structured sub-object
        # (e.g. targeted_recursion.sub_analysis) legitimately is one
        if isinstance(v, str):
            return bool(v.strip())
        if isinstance(v, list):
            return any(field_filled(x) for x in v)
        if isinstance(v, dict):
            return bool(section_text(v).strip())
        return False

    def item_incomplete(item, id_field, main_field):
        ident = item.get(id_field)
        main = item.get(main_field)
        return not (isinstance(ident, str) and ident.strip()
                    and isinstance(main, str) and main.strip())

    for section in required:
        value = analysis.get(section)
        if section_is_empty(value):
            report(section, "REJECT", "SECTION_MISSING",
                   "required section absent or empty")
        elif not isinstance(value, (str, list, dict)):
            # an unexpected scalar (number, boolean) is not an analysis:
            # explicit REJECT, never an accidental ACCEPT
            report(section, "REJECT", "SECTION_INVALID_TYPE",
                   "unexpected type {}: string, list or object expected"
                   .format(type(value).__name__))
        elif section in LIST_SECTIONS:
            if not isinstance(value, list):
                report(section, "REJECT", "SECTION_INVALID_TYPE",
                       "list of structured objects expected, got {}"
                       .format(type(value).__name__))
            elif any(not isinstance(item, dict) for item in value):
                report(section, "REJECT", "SECTION_ITEM_NOT_STRUCTURED",
                       "every item must be a structured object "
                       "(fields *_id, statement, ...), not free text")
            else:
                id_field, main_field = ITEM_REQUIRED[section]
                bad = ["#{}".format(i + 1) for i, item in enumerate(value)
                       if item_incomplete(item, id_field, main_field)]
                if bad:
                    report(section, "REJECT", "ITEM_FIELDS_MISSING",
                           "item(s) {}: non-empty textual fields '{}' and '{}' required"
                           .format(", ".join(bad), id_field, main_field))
                else:
                    report(section, "ACCEPT", None, None)
        elif section in DICT_SECTIONS and not isinstance(value, dict):
            report(section, "REJECT", "SECTION_INVALID_TYPE",
                   "structured object expected (statement + references), got {}"
                   .format(type(value).__name__))
        elif section in STRUCT_REQUIRED:
            fields = STRUCT_REQUIRED[section]
            if not isinstance(value, dict):
                report(section, "REJECT", "SECTION_INVALID_TYPE",
                       "structured object expected (fields {}), got {}"
                       .format(", ".join(fields), type(value).__name__))
            else:
                missing = [f for f in fields if not field_filled(value.get(f))]
                if missing:
                    report(section, "REJECT", "SECTION_FIELDS_MISSING",
                           "required fields absent or empty: {}".format(", ".join(missing)))
                else:
                    report(section, "ACCEPT", None, None)
        else:
            report(section, "ACCEPT", None, None)

    # CANONICAL identifiers: only the declared id fields of known sections.
    # An id invented in an unknown ("junk") section grounds nothing, and a
    # duplicated id is refused.
    known_ids = set()
    for sec, (idf, mainf) in ITEM_REQUIRED.items():
        items = analysis.get(sec)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if item_incomplete(item, idf, mainf):
                # an incomplete item (id without substance) grounds NOTHING,
                # even in a section not required at this depth
                continue
            v = item.get(idf)
            if isinstance(v, str) and v.strip():
                vid = v.strip()
                if vid in known_ids:
                    report(sec, "REJECT", "DUPLICATE_ID",
                           "identifier '{}' used more than once".format(vid))
                else:
                    known_ids.add(vid)

    hypotheses = analysis.get("hypotheses") or []
    if not isinstance(hypotheses, list):
        hypotheses = []
    alternatives = analysis.get("alternatives") or []
    if not isinstance(alternatives, list):
        alternatives = []

    # 2. depth bounds
    max_h = constraints.get("max_hypotheses")
    if isinstance(max_h, int) and len(hypotheses) > max_h:
        report("hypotheses", "REJECT", "TOO_MANY_HYPOTHESES",
               "{} hypotheses, maximum {} at this depth".format(
                   len(hypotheses), max_h))
    min_a = constraints.get("min_alternatives")
    if isinstance(min_a, int) and alternatives and len(alternatives) < min_a:
        report("alternatives", "REJECT", "NOT_ENOUGH_ALTERNATIVES",
               "{} alternative(s), minimum {} at this depth".format(
                   len(alternatives), min_a))

    # 3. falsification with teeth
    frule = rules.get("FALSIFICATION_REQUIRED")
    if frule and hypotheses:
        scope = frule.get("scope")
        targets = hypotheses if scope == "each" else hypotheses[:1]
        for i, h in enumerate(targets):
            test = h.get("falsification_test") if isinstance(h, dict) else None
            if not isinstance(test, str) or not test.strip():
                report("hypotheses", "REJECT", "HYPOTHESIS_NOT_FALSIFIABLE",
                       "hypothesis {} without an observable falsification_test (scope {})"
                       .format((h.get("hypothesis_id") if isinstance(h, dict) else None)
                               or "#{}".format(i + 1), scope))

    # 4. anti-anchoring: alternative vs primary hypothesis
    if "ALTERNATIVE_DUPLICATES_PRIMARY" in rules and hypotheses and alternatives:
        primary = hypotheses[0]
        primary_stmt = primary.get("statement", "") if isinstance(primary, dict) else str(primary)
        if not isinstance(primary_stmt, str):
            # a non-textual statement is not comparable: explicit reject,
            # never a crash nor a free pass
            report("hypotheses", "REJECT", "STATEMENT_NOT_TEXT",
                   "primary hypothesis: statement of type {} instead of text"
                   .format(type(primary_stmt).__name__))
            primary_stmt = ""
        for i, alt in enumerate(alternatives):
            alt_stmt = alt.get("statement", "") if isinstance(alt, dict) else str(alt)
            label = ((alt.get("alternative_id") if isinstance(alt, dict) else None)
                     or "#{}".format(i + 1))
            if not isinstance(alt_stmt, str):
                report("alternatives", "REJECT", "STATEMENT_NOT_TEXT",
                       "alternative {}: statement of type {} instead of text"
                       .format(label, type(alt_stmt).__name__))
                continue
            sim = similarity(primary_stmt, alt_stmt)
            if normalized_equal(primary_stmt, alt_stmt):
                report("alternatives", "REJECT", "ALTERNATIVE_DUPLICATES_PRIMARY",
                       "alternative {} duplicates the primary hypothesis (identical idea)"
                       .format(label))
            elif sim >= SIMILARITY_WARN:
                report("alternatives", "WARNING", "ALTERNATIVE_SIMILAR_TO_PRIMARY",
                       "alternative {} close to the primary hypothesis (similarity {:.2f})"
                       .format(label, sim))

    # 5. second-order effects distinct from first order
    risks = analysis.get("second_order_risks") or []
    if "SECOND_ORDER_SIMILAR_TO_FIRST" in rules and isinstance(risks, list):
        for i, r in enumerate(risks):
            if not isinstance(r, dict):
                continue
            first = r.get("first_order_effect", "")
            second = r.get("second_order_effect", "")
            label = r.get("risk_id") or "#{}".format(i + 1)
            bad_types = [n for n, v in (("first_order_effect", first),
                                        ("second_order_effect", second))
                         if v is not None and v != "" and not isinstance(v, str)]
            if bad_types:
                report("second_order_risks", "REJECT", "EFFECT_NOT_TEXT",
                       "risk {}: non-textual field(s) {}".format(label, ", ".join(bad_types)))
                continue
            # a second-order risk WITHOUT both effects says nothing:
            # both fields are required
            missing_eff = [n for n, v in (("first_order_effect", first),
                                          ("second_order_effect", second))
                           if not (isinstance(v, str) and v.strip())]
            if missing_eff:
                report("second_order_risks", "REJECT", "EFFECT_MISSING",
                       "risk {}: required effect(s) absent: {}"
                       .format(label, ", ".join(missing_eff)))
                continue
            sim = similarity(first, second)
            if normalized_equal(first, second):
                report("second_order_risks", "REJECT", "SECOND_ORDER_REPEATS_FIRST",
                       "risk {}: the second-order effect repeats the first order "
                       "(identical idea)".format(label))
            elif sim >= SIMILARITY_WARN:
                report("second_order_risks", "WARNING", "SECOND_ORDER_SIMILAR_TO_FIRST",
                       "risk {}: first- and second-order effects are close "
                       "(similarity {:.2f})".format(label, sim))

    # 6. a pre-mortem that does not copy the risks
    premortem = analysis.get("premortem")
    if ("PREMORTEM_COPIES_RISKS" in rules and not section_is_empty(premortem)
            and isinstance(risks, list)):
        # compare each semantic FIELD of the pre-mortem separately as well as
        # the flattened whole: copying a risk into 'story' and padding another
        # field with distinct text diluted the flattened comparison under the
        # threshold and slipped through.
        ptexts = [section_text(premortem)]
        if isinstance(premortem, dict):
            for v in premortem.values():
                if isinstance(v, str) and v.strip():
                    ptexts.append(v)
                elif isinstance(v, list):
                    for x in v:
                        if isinstance(x, str) and x.strip():
                            ptexts.append(x)
        for i, r in enumerate(risks):
            if not isinstance(r, dict):
                continue
            label = r.get("risk_id") or "#{}".format(i + 1)
            # compare to the WHOLE risk AND to each semantic field separately:
            # copying a single effect (diluted in the full risk text) slipped
            # under the threshold.
            candidates = [section_text(r)]
            for fld in ("risk", "first_order_effect", "second_order_effect", "mitigation"):
                v = r.get(fld)
                if isinstance(v, str) and v.strip():
                    candidates.append(v)
            sim = max(similarity(pt, cand) for pt in ptexts for cand in candidates)
            if any(normalized_equal(pt, cand) for pt in ptexts for cand in candidates):
                report("premortem", "REJECT", "PREMORTEM_COPIES_RISKS",
                       "the pre-mortem copies risk {} (identical idea)".format(label))
            elif sim >= SIMILARITY_WARN:
                report("premortem", "WARNING", "PREMORTEM_SIMILAR_TO_RISKS",
                       "pre-mortem close to risk {} (similarity {:.2f})"
                       .format(label, sim))

    # 7. referenced synthesis: a conclusion points at what grounds it
    synthesis = analysis.get("synthesis")
    if "SYNTHESIS_REFERENCES" in rules and not section_is_empty(synthesis):
        stmt = synthesis.get("statement") if isinstance(synthesis, dict) else None
        if not isinstance(stmt, str) or not stmt.strip():
            # references without a conclusion are not a synthesis
            report("synthesis", "REJECT", "SYNTHESIS_WITHOUT_STATEMENT",
                   "the synthesis must carry a non-empty textual statement")
        refs = synthesis.get("references") if isinstance(synthesis, dict) else None
        known = known_ids
        if not isinstance(refs, list) or not refs:
            report("synthesis", "REJECT", "SYNTHESIS_WITHOUT_REFERENCES",
                   "the synthesis references no identifier from upstream sections")
        else:
            unknown = [x for x in refs if not (isinstance(x, str) and x.strip() in known)]
            if unknown:
                report("synthesis", "REJECT", "SYNTHESIS_REFERENCES_UNKNOWN_ID",
                       "nonexistent identifiers referenced: {}".format(
                           ", ".join(str(u) for u in unknown)))

    # 8. filler-phrase density (measures the proportion, not the presence)
    if "GENERIC_DENSITY" in rules and blacklist:
        for section in required:
            value = analysis.get(section)
            if section_is_empty(value):
                continue
            hollow, total = generic_density(semantic_text(value), blacklist)
            if hollow == 0 or total == 0:
                continue
            if total >= 2 and hollow / float(total) > GENERIC_DENSITY_REJECT:
                report(section, "REJECT", "GENERIC_DENSITY_HIGH",
                       "{} hollow sentence(s) out of {}: section mostly filler"
                       .format(hollow, total))
            else:
                report(section, "WARNING", "GENERIC_PHRASE_PRESENT",
                       "{} hollow sentence(s) out of {} (tolerated, densify)"
                       .format(hollow, total))

    section_results = [results[s] for s in sorted(results)]
    for r in section_results:
        r["details"] = " ; ".join(r["details"]) if r["details"] else ""
    overall = "ACCEPT"
    for r in section_results:
        if VERDICT_ORDER[r["verdict"]] > VERDICT_ORDER[overall]:
            overall = r["verdict"]
    return {
        "verdict": overall,
        "frame_hash": frame.get("frame_hash", ""),
        "section_results": section_results,
    }
