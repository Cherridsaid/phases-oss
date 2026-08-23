"""Deterministic, versioned, **neutral** base contexts for the OSS Multidim store.

These seed contexts ship with the package. They contain generic analysis grids
only -- no personal data, no project names, no local paths. A guard
(:func:`assert_neutral`) enforces this automatically so a personal context can
never leak into the published package.

The base store is deterministic: the same version always produces the same
contexts, so the shipped grid is reproducible.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List

# Bumped when the seed contexts change. Distinct from any personal store schema.
BASE_VERSION = 1

# Bumped when a keyword is ADDED to a seed context. An existing store keeps its
# own version and is enriched once by the additive migration -- never reset, so
# bumping this is safe where bumping BASE_VERSION would discard learned traps.
SEED_KEYWORDS_VERSION = 1


def _ax(name: str, question: str, sublenses: List[str]) -> Dict:
    return {"name": name, "question": question, "sublenses": list(sublenses)}


def _ctx(name: str, description: str, keywords: List[str], axes: List[Dict]) -> Dict:
    return {"name": name, "description": description, "keywords": list(keywords), "axes": axes}


def base_contexts() -> List[Dict]:
    """The neutral seed contexts. Pure data, deterministic."""
    return [
        _ctx(
            "generic",
            "Base grid, a general-purpose thinking lens. Used when no specific domain fits.",
            [],
            [
                _ax("Structure", "What the subject is made of, which parts, how they fit together",
                    ["Parts and sub-parts", "Hierarchy and dependencies", "What holds it together"]),
                _ax("Causality", "Which causes produce it, which effects follow",
                    ["Direct vs root causes", "First- and second-order effects", "Feedback loops"]),
                _ax("Time", "Past state, present state, future trajectory",
                    ["How we got here", "Current state", "Trajectory and tipping points"]),
                _ax("Scale", "Seen very close (detail) then very far (system), what changes",
                    ["Micro view (the detail)", "Macro view (the system)", "What only appears at one scale"]),
                _ax("Tensions", "Which internal tensions, paradoxes or opposing forces act",
                    ["Opposing forces", "Hidden trade-offs", "What cannot be true at once"]),
                _ax("Blind spots", "What is unsaid, the implicit assumptions, the blind angles",
                    ["Unstated assumptions", "What we avoid looking at", "The angle nobody takes"]),
                _ax("Lateral links", "What else the subject connects to in neighbouring domains",
                    ["Analogies elsewhere", "External dependencies", "Side effects on the rest"]),
                _ax("Emergence", "Which global behaviour appears that the parts alone do not explain",
                    ["Whole-only properties", "Threshold effects", "Self-sustaining dynamics"]),
            ],
        ),
        _ctx(
            "code_review",
            "Reviewing a code change: correctness, risk, tests, maintainability.",
            # same whole-token rule as 'decision': list the inflections that a
            # real review subject actually uses ('tests', 'refactoring', ...)
            ["code", "review", "reviews", "diff", "diffs", "pull request",
             "refactor", "refactoring", "bug", "bugs",
             "regression", "regressions", "test", "tests",
             "coverage", "lint", "api", "breaking change", "maintainability"],
            [
                _ax("Correctness", "Does the change do what it claims, on the happy path and edges?",
                    ["Stated behaviour vs actual", "Edge cases and inputs", "Off-by-one and boundaries"]),
                _ax("Contract", "What interface does it expose or depend on, and does it hold?",
                    ["Public API surface", "Backward compatibility", "Assumptions on callers"]),
                _ax("Error handling", "What happens on failure, and does it fail safely?",
                    ["Failure paths", "Fail-open vs fail-closed", "Actionable error messages"]),
                _ax("State and data", "What state is stored or mutated, and how is it encoded?",
                    ["Persisted state", "Shared mutable state", "Encoding and serialization"]),
                _ax("Tests", "Is the behaviour proven deterministically, including negatives?",
                    ["Deterministic proof", "Real entry point vs internals", "Negative and regression cases"]),
                _ax("Security", "Which untrusted input reaches which sink, and who validates?",
                    ["Untrusted inputs", "Trust boundaries", "Injection and traversal"]),
                _ax("Performance", "Where is the cost, and does it scale with the input?",
                    ["Hot path cost", "Complexity vs input", "Resource and memory use"]),
                _ax("Maintainability", "Added surface, clarity, who maintains it next?",
                    ["Added surface", "Readability", "Simplicity vs completeness"]),
            ],
        ),
        _ctx(
            "technical_writing",
            "Long-form writing: articles, documentation, reports.",
            ["write", "writing", "article", "documentation", "docs", "report", "readme",
             "paragraph", "narrative", "content", "style", "audience"],
            [
                _ax("Reader", "Who is addressed, what they already know, what they expect",
                    ["Who the reader is", "What they already know", "What they expect"]),
                _ax("Thesis", "The central idea, the angle, what should change for the reader",
                    ["Core idea in one sentence", "Chosen angle", "Intended effect"]),
                _ax("Structure", "How the argument unfolds from start to end",
                    ["Opening promise", "Argument progression", "Resolution"]),
                _ax("Evidence", "What the claims rest on",
                    ["Nature of the evidence", "Source strength", "Unsupported claims"]),
                _ax("Tone", "Voice, register, distance to the reader",
                    ["Voice and personality", "Register", "Distance to the reader"]),
                _ax("Counter-arguments", "What a hostile reader would object",
                    ["Strongest objection", "Blind spot of the thesis", "Honest answer"]),
                _ax("Rhythm", "Where it drags, where it accelerates, balance",
                    ["Where it drags", "Where it accelerates", "Beginning-middle-end balance"]),
                _ax("Gaps", "The hole the reader will feel",
                    ["Unanswered question", "Missing example", "Missing transition"]),
            ],
        ),
        _ctx(
            "decision",
            "Making a decision: options, trade-offs, reversibility, risk.",
            # keywords match whole tokens, so every realistic inflection has to
            # be listed: 'option' alone never matches 'options', and a decision
            # subject that misses here silently falls back to the generic grid.
            ["decision", "decisions", "decide", "deciding",
             "choice", "choices", "option", "options",
             "alternative", "alternatives",
             "trade-off", "tradeoff", "trade-offs", "tradeoffs",
             "compare", "comparing", "comparison",
             "should i", "should we", "whether",
             "risk", "risks", "reversible", "criteria", "criterion"],
            [
                _ax("Real question", "What decision is actually being made, and by when?",
                    ["The actual choice", "Constraints and deadline", "Who decides"]),
                _ax("Options", "What are the genuinely distinct options, including doing nothing?",
                    ["Distinct options", "The null option", "Hidden options"]),
                _ax("Criteria", "What matters, and how is each option scored?",
                    ["What matters", "Weighting", "Measurable vs felt"]),
                _ax("Trade-offs", "What each option gives up",
                    ["Cost of each option", "What is sacrificed", "Second-order effects"]),
                _ax("Reversibility", "How hard is it to undo if wrong?",
                    ["One-way vs two-way door", "Cost to reverse", "Point of no return"]),
                _ax("Risk", "What could go wrong, and how likely and severe?",
                    ["Failure modes", "Likelihood", "Severity"]),
                _ax("Assumptions", "Which beliefs, if false, flip the decision?",
                    ["Load-bearing beliefs", "How to test them", "Cost if wrong"]),
                _ax("Blind spots", "What is not on the table that should be?",
                    ["Unconsidered angle", "Silent constraint", "Whose view is missing"]),
            ],
        ),
    ]


# --------------------------------------------------------------------------- #
# Neutrality guard: no personal data may ever ship in the base store.
# --------------------------------------------------------------------------- #
# Case-insensitive substrings that must never appear in the shipped contexts.
# GENERIC leak indicators only -- absolute-path prefixes, home/config dirs and
# obvious secret markers. Deliberately nobody's name or private project is
# hardcoded here: the PUBLISHED package must not itself carry personal tokens
# (that would be the very leak this guard exists to prevent). Downstream
# maintainers can extend this tuple with their own private markers locally.
FORBIDDEN_TOKENS = (
    "c:/users",
    "c:\\users",
    "/home/",
    "/users/",
    ".multidim",
    ".mempalace",
    ".ssh/",
    "-----begin",
    "@gmail.com",
)


def _extra_forbidden_tokens() -> list:
    """Private, deployment-specific denylist supplied out-of-band via the
    ``PHASES_OSS_EXTRA_FORBIDDEN`` env var (comma-separated). This lets a private
    maintainer catch THEIR own personal markers (names, project codenames)
    without hardcoding them into the published source -- which would itself be
    the leak the guard exists to prevent."""
    raw = os.environ.get("PHASES_OSS_EXTRA_FORBIDDEN", "")
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


def assert_neutral(contexts=None) -> None:
    """Raise ``AssertionError`` if any forbidden token is present.

    Scans the JSON serialization of the base contexts (names, descriptions,
    keywords, axes, sublenses) so nothing forbidden can slip through any field.
    Checks the shipped generic markers PLUS any private denylist from
    ``PHASES_OSS_EXTRA_FORBIDDEN`` (see :func:`_extra_forbidden_tokens`).
    """
    if contexts is None:
        contexts = base_contexts()
    blob = json.dumps(contexts, ensure_ascii=False).lower()
    tokens = list(FORBIDDEN_TOKENS) + _extra_forbidden_tokens()
    hits = [tok for tok in tokens if tok in blob]
    if hits:
        raise AssertionError(
            "base contexts are not neutral, forbidden token(s): %s" % ", ".join(sorted(set(hits)))
        )
