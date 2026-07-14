"""v2 frame building for the bundled Multidim server (deterministic contract).

A *frame* is the JSON structure returned by ``multidim_analyze`` with
``format: "v2"``: a deterministic contract the calling LLM fills in section by
section, later checked by ``multidim_validate``. The frame carries its own
integrity hash (``frame_hash``) so the validator can rebuild it from the store
and refuse a tampered or stale frame.

This module also owns *traps* (learned lessons): each trap becomes a mandatory
question injected into future v2 frames whenever one of its triggers matches
the subject. Traps are written only through ``multidim_learn``.

Ported from the canonical Multidim v2 engine; pure standard library.
"""

from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
from typing import Dict, List, Optional, Tuple

# Accent-fold map built from code points so the SOURCE stays pure ASCII (no
# accented literal can be mojibaked in transit). Same table as the server used.
_FOLD_ORDS = {
    0xE0: "a", 0xE2: "a", 0xE4: "a", 0xE1: "a", 0xE3: "a",  # a: grave circ tre acute tilde
    0xE9: "e", 0xE8: "e", 0xEA: "e", 0xEB: "e",              # e: acute grave circ tre
    0xEE: "i", 0xEF: "i", 0xED: "i",                          # i: circ tre acute
    0xF4: "o", 0xF6: "o", 0xF3: "o", 0xF5: "o",              # o: circ tre acute tilde
    0xF9: "u", 0xFB: "u", 0xFC: "u", 0xFA: "u",              # u: grave circ tre acute
    0xE7: "c", 0xF1: "n",                                     # c-cedilla, n-tilde
}
_FOLD = {chr(cp): rep for cp, rep in _FOLD_ORDS.items()}


def fold(s: str) -> str:
    # NFKD-normalise then drop combining marks BEFORE folding, so a decomposed
    # accent (e + U+0301) and its precomposed form (U+00E9) fold identically --
    # otherwise the same word in two Unicode forms would route to two different
    # contexts. The _FOLD map then mops up any precomposed leftovers.
    decomposed = unicodedata.normalize("NFKD", s.lower())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return "".join(_FOLD.get(ch, ch) for ch in stripped)


def tokens_of(folded: str):
    # Unicode-aware: non-Latin scripts (CJK, Cyrillic, Greek) must tokenize
    # too, otherwise they produce ZERO tokens and every similarity/duplicate
    # check silently goes blind on such text.
    out = set()
    cur = []
    for ch in folded:
        if ch.isalnum():
            cur.append(ch)
        else:
            if cur:
                out.add("".join(cur))
                cur = []
    if cur:
        out.add("".join(cur))
    return out


ANALYSIS_SCHEMA_VERSION = 2

# Depth bounds: depth raises the bar of the CHECK, not the length of the text.
DEPTH_PARAMS = {
    "core": {
        "max_validation_rounds": 1,
        "max_hypotheses": 2,
        "min_alternatives": 1,
        "premortem_required": False,
        "falsification_scope": "none",     # no falsification requirement
        "required_sections": [
            "facts", "hypotheses", "alternatives", "cross_talk", "synthesis",
        ],
    },
    "deep": {
        "max_validation_rounds": 2,
        "max_hypotheses": 5,
        "min_alternatives": 2,
        "premortem_required": True,
        "falsification_scope": "primary",  # at least the primary hypothesis
        "required_sections": [
            "facts", "hypotheses", "alternatives", "hidden_dependencies",
            "second_order_risks", "contradictions", "open_questions",
            "decision_criteria", "cross_talk", "premortem", "synthesis",
        ],
    },
    "full": {
        "max_validation_rounds": 3,
        "max_hypotheses": None,            # competing hypotheses, no cap
        "min_alternatives": 2,
        "premortem_required": True,
        "falsification_scope": "each",     # EVERY hypothesis
        "required_sections": [
            "facts", "hypotheses", "alternatives", "hidden_dependencies",
            "second_order_risks", "contradictions", "open_questions",
            "decision_criteria", "cross_talk", "premortem",
            # v1 full-mode carry-over: the most loaded tension is re-run alone
            # in a dedicated sub-grid (one pass, not a loop)
            "targeted_recursion", "synthesis",
        ],
    },
}


# ---------------------------------------------------------------------------
# Structural section schemas -- SINGLE SOURCE shared with validate.py, and
# PUBLISHED in every v2 frame (section_schemas) so a client knows the expected
# shapes up front instead of discovering them through SECTION_INVALID_TYPE.
# ---------------------------------------------------------------------------

# list sections: (identifier field, main textual field) per item
ITEM_REQUIRED = {
    "facts": ("fact_id", "statement"),
    "hypotheses": ("hypothesis_id", "statement"),
    "alternatives": ("alternative_id", "statement"),
    "hidden_dependencies": ("dependency_id", "dependency"),
    "second_order_risks": ("risk_id", "risk"),
    "contradictions": ("contradiction_id", "contradiction"),
    "open_questions": ("question_id", "question"),
    "decision_criteria": ("criterion_id", "criterion"),
}
LIST_SECTIONS = set(ITEM_REQUIRED)

# object sections with required substance fields
STRUCT_REQUIRED = {
    "cross_talk": ("tensions", "blind_spots"),
    "premortem": ("story",),
    "targeted_recursion": ("focus", "sub_analysis"),
}

# the synthesis must be an object carrying a statement and references
DICT_SECTIONS = {"synthesis"}
SYNTHESIS_FIELDS = ("statement", "references")


def section_schemas_for(required_sections, falsification_scope):
    """Machine-readable shape of every required section, embedded in the frame."""
    schemas = {}
    for s in required_sections:
        if s in ITEM_REQUIRED:
            id_field, main_field = ITEM_REQUIRED[s]
            schema = {"type": "list of objects",
                      "item_required_fields": [id_field, main_field]}
            if s == "second_order_risks":
                schema["item_required_fields"] = [
                    id_field, main_field, "first_order_effect", "second_order_effect"]
            if s == "hypotheses" and falsification_scope != "none":
                schema["note"] = ("falsification_test required on {} hypothesis"
                                  .format("every" if falsification_scope == "each"
                                          else "the primary"))
            schemas[s] = schema
        elif s in DICT_SECTIONS:
            schemas[s] = {"type": "object",
                          "required_fields": list(SYNTHESIS_FIELDS),
                          "note": "references must be identifiers from upstream sections"}
        elif s in STRUCT_REQUIRED:
            schemas[s] = {"type": "object",
                          "required_fields": list(STRUCT_REQUIRED[s])}
        else:
            schemas[s] = {"type": "string, list or object", "required_fields": []}
    return schemas


def normalize_statement(s: str) -> str:
    """Dedup key for a trap: folded statement, tokens in ORIGINAL order.

    Order is preserved on purpose: 'client pays supplier' and 'supplier pays
    client' are two different lessons, not one. A sorted key would collapse
    them and the first trap would silently be overwritten by the second.
    """
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
    return " ".join(out)


def sanitize_trap(context_name: str, raw) -> Tuple[Optional[Dict], Optional[str]]:
    """Validate and normalise one trap received by ``multidim_learn``.

    Returns ``(trap, None)`` or ``(None, actionable error message)``.
    """
    if not isinstance(raw, dict):
        return None, "each trap must be an object"
    statement = raw.get("statement")
    if not isinstance(statement, str) or not statement.strip():
        return None, "trap without 'statement'"
    question = raw.get("mandatory_question")
    if not isinstance(question, str) or not question.strip():
        return None, "trap without 'mandatory_question'"
    triggers = raw.get("triggers")
    if (not isinstance(triggers, list) or not triggers
            or not all(isinstance(t, str) and t.strip() for t in triggers)):
        return None, "trap without 'triggers' (non-empty array of strings)"
    severity = raw.get("severity", "medium")
    if severity not in ("low", "medium", "high"):
        severity = "medium"
    trap_id = raw.get("trap_id")
    if not isinstance(trap_id, str) or not trap_id.strip():
        # deterministic id derived from context + normalised statement.
        # 16 hex chars (64 bits): 8 chars (32 bits) produced real birthday
        # collisions between unrelated lessons; upsert_traps additionally
        # refuses any residual derived-id collision instead of merging.
        digest = hashlib.sha256(
            (context_name + "|" + normalize_statement(statement)).encode("utf-8")
        ).hexdigest()
        trap_id = "trap_{}_{}".format(fold(context_name).replace(" ", "_")[:24], digest[:16])
    active = raw.get("active")
    if not isinstance(active, bool):
        active = True
    return {
        # ALWAYS stripped: ' id ' and 'id' must be one identifier, not two
        # traps that a single answer later satisfies at once
        "trap_id": trap_id.strip(),
        "statement": statement.strip(),
        "mandatory_question": question.strip(),
        "triggers": [t.strip() for t in triggers],
        "severity": severity,
        "source": raw.get("source") if isinstance(raw.get("source"), str) else "explicit_learning",
        "active": active,
        "schema_version": 1,
    }, None


def upsert_traps(context: Dict, raw_traps) -> Tuple[int, int, List[str]]:
    """Add or update traps in a context, deduplicated by trap_id then by
    normalised statement. Returns ``(added, updated, errors)``."""
    existing = context.setdefault("traps", [])

    def build_indexes():
        by_id = {t.get("trap_id"): t for t in existing if isinstance(t, dict)}
        by_stmt = {normalize_statement(t.get("statement", "")): t
                   for t in existing if isinstance(t, dict)}
        return by_id, by_stmt

    by_id, by_stmt = build_indexes()
    added, updated, errors = 0, 0, []
    for raw in raw_traps:
        explicit_id = (isinstance(raw, dict) and isinstance(raw.get("trap_id"), str)
                       and bool(raw["trap_id"].strip()))
        trap, err = sanitize_trap(context["name"], raw)
        if err:
            errors.append(err)
            continue
        match_id = by_id.get(trap["trap_id"])
        match_stmt = by_stmt.get(normalize_statement(trap["statement"]))
        if (not explicit_id and match_id is not None
                and normalize_statement(match_id.get("statement", ""))
                != normalize_statement(trap["statement"])):
            # the DERIVED id lands on a trap that carries a DIFFERENT lesson:
            # truncated-hash collision. Merging would silently destroy one of
            # the two lessons -- refuse, ask for an explicit id instead.
            errors.append(
                "derived trap_id collision: '{}' already belongs to a different "
                "lesson ('{}'); provide an explicit trap_id".format(
                    trap["trap_id"], match_id.get("statement", "")))
            continue
        if match_id is not None and match_stmt is not None and match_id is not match_stmt:
            # id and statement each match a DIFFERENT existing trap: mutating
            # either would duplicate the other's lesson. Refuse the collision,
            # an explicit manual merge is required.
            errors.append(
                "collision: trap_id '{}' and statement '{}' belong to two distinct "
                "traps; merge them explicitly".format(trap["trap_id"], trap["statement"]))
            continue
        current = match_id or match_stmt
        if current is not None:
            if not explicit_id:
                # resending a known lesson WITHOUT an explicit trap_id must
                # never replace the existing (possibly custom, referenced)
                # identifier with a derived one
                cur_id = current.get("trap_id")
                if isinstance(cur_id, str) and cur_id.strip():
                    trap["trap_id"] = cur_id
            if current != trap:
                current.update(trap)   # same lesson: update (including active:false)
                updated += 1
                # the id/statement keys of ``current`` may have changed:
                # reindex, otherwise the next trap of the same batch matches a
                # stale index and a lesson silently disappears
                by_id, by_stmt = build_indexes()
        else:
            existing.append(trap)
            by_id[trap["trap_id"]] = trap
            by_stmt[normalize_statement(trap["statement"])] = trap
            added += 1
    return added, updated, errors


def trap_matches(trap: Dict, folded_subject: str, subject_tokens) -> bool:
    """A trap is injected when one of its triggers matches the subject.

    Matching rule is IDENTICAL to context-keyword matching (``detect_context``)
    and to the canonical Multidim engine: multi-word / hyphenated triggers match
    as substrings, single-word triggers match on whole tokens. Known shared
    limitation (with the canonical engine and with keyword detection): a
    separator-less script (e.g. CJK) yields one big token, so a single-token
    trigger embedded in a longer run does not match. Fixing this belongs to BOTH
    engines at once, not the bundle alone, to preserve parity. Defensive: a
    scalar or missing ``triggers`` never matches and never crashes frame building."""
    triggers = trap.get("triggers", [])
    if not isinstance(triggers, list):
        return False
    for t in triggers:
        if not isinstance(t, str):
            continue
        tf = fold(t)
        if not tf:
            continue
        if " " in tf or "-" in tf:
            if tf in folded_subject:
                return True
        elif tf in subject_tokens:
            return True
    return False


def select_traps(context: Dict, subject: str) -> List[Dict]:
    """Active traps of the context whose triggers match the subject."""
    folded = fold(subject)
    toks = tokens_of(folded)
    out = []
    for trap in context.get("traps", []):
        if not isinstance(trap, dict):
            continue
        if trap.get("active") is not True:
            continue  # active:false = lesson disabled, never injected
        if trap_matches(trap, folded, toks):
            out.append(trap)
    return out


def frame_hash_of(frame: Dict) -> str:
    """Deterministic hash of the FULL canonical frame, EXCLUDING the frame_id
    and frame_hash fields themselves. Excluding them means recomputing the hash
    on an already-hashed frame reproduces the stored value (self-verifiable),
    instead of feeding the previous hash back into itself. Any content change --
    including an axis added via learn without a store version bump -- changes
    the hash."""
    basis_src = {k: v for k, v in frame.items()
                 if k not in ("frame_id", "frame_hash")}
    basis = json.dumps(basis_src, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def build_frame(store: Dict, context: Dict, subject: str, score: int, depth: str) -> Dict:
    """v2 frame: deterministic structure for the calling LLM to fill in.

    No pseudo-hypothesis is generated; the store is never written. Host hints
    are optional and neutral: no mandatory section depends on them, and a
    generic MCP client can ignore them entirely.
    """
    params = DEPTH_PARAMS[depth]
    traps = select_traps(context, subject)

    if score == -1:
        explanation = "context forced by the caller"
    elif score == 0:
        explanation = ("no context keyword matches the subject: falling back "
                       "to the generic grid (general-purpose lens)")
    else:
        explanation = "{} context keyword(s) found in the subject".format(score)

    validation_rules = [
        {"rule": "SECTION_MISSING",
         "detail": "every section of required_sections must exist and be non-empty"},
        {"rule": "ALTERNATIVE_DUPLICATES_PRIMARY",
         "detail": "an alternative that duplicates the primary hypothesis is rejected; "
                   "ambiguous similarity = WARNING"},
        {"rule": "SYNTHESIS_REFERENCES",
         "detail": "the synthesis must reference existing identifiers from upstream sections"},
        {"rule": "GENERIC_DENSITY",
         "detail": "a section mostly made of blacklisted filler phrases is rejected; "
                   "an isolated phrase = WARNING"},
    ]
    if params["falsification_scope"] != "none":
        validation_rules.append(
            {"rule": "FALSIFICATION_REQUIRED", "scope": params["falsification_scope"],
             "detail": "hypothesis without an observable falsification_test: REJECT"})
    if depth in ("deep", "full"):
        validation_rules.append(
            {"rule": "SECOND_ORDER_SIMILAR_TO_FIRST",
             "detail": "second-order effect identical to the first order: REJECT; "
                       "partial resemblance: WARNING"})
        validation_rules.append(
            {"rule": "PREMORTEM_COPIES_RISKS",
             "detail": "a pre-mortem that copies the listed risks: REJECT; "
                       "resemblance: WARNING"})

    frame = {
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "store_version": store.get("version", 0),
        "context": {"name": context["name"], "score": score, "explanation": explanation},
        "depth": depth,
        "subject": subject,
        # deepcopy: a shallow dict() still shares the inner sublenses/triggers
        # LISTS with the store, so mutating a returned frame would corrupt the
        # store (and the frame_hash is computed over these very structures)
        "axes": copy.deepcopy(context.get("axes", [])),
        "sub_lenses_note": "sub-lenses are starting points, disposable by default",
        "cross_talk_required": True,
        "mandatory_questions": (
            [a.get("question", "") for a in context.get("axes", [])]
            + [t.get("mandatory_question", "") for t in traps if t.get("mandatory_question")]),
        "learned_traps": copy.deepcopy(traps),
        "required_sections": list(params["required_sections"]),
        "section_schemas": section_schemas_for(params["required_sections"],
                                               params["falsification_scope"]),
        "constraints": {
            "max_hypotheses": params["max_hypotheses"],
            "min_alternatives": params["min_alternatives"],
            "premortem_required": params["premortem_required"],
            "falsification_scope": params["falsification_scope"],
        },
        "validation_rules": validation_rules,
        "max_validation_rounds": params["max_validation_rounds"],
        # defence in depth: a malformed blacklist (null, scalar) must never
        # crash frame building even if it slipped past the load-time check
        "generic_blacklist": (list(store["generic_blacklist"])
                              if isinstance(store.get("generic_blacklist"), list)
                              else []),
        "host_hints": {
            "optional": True,
            "note": ("host-environment guidance; a generic MCP client can ignore "
                     "these without breaking anything"),
            "hints": [
                "Persist the synthesis wherever your workflow keeps analysis output.",
                "Call multidim_learn ONLY if a lens or a trap proved reusable "
                "beyond this subject.",
            ],
        },
    }
    fhash = frame_hash_of(frame)
    frame["frame_hash"] = fhash
    frame["frame_id"] = "frame_" + fhash[:24]
    return frame
