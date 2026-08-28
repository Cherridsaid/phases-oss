"""The 71 canonical ordinals: one skill, one phase, frozen order.

The pipeline is a constant, not a computation. ``ORDINALS`` is built once at
import time and exposed as a tuple so no caller -- and no model -- can reorder,
insert or drop a phase mid-run. A phase that does not apply to the target is
still *visited*: it gets a status and a typed reason, and the next phase is
``ordinal + 1``. Nothing is ever removed from the sequence.

Two orthogonal axes are recorded per ordinal, and confusing them breaks the
network policy:

``plane``
    ``execution`` = the phase's real work is a local deterministic tool
    (semgrep, gitleaks, a test runner). It runs offline with a scrubbed
    environment. ``model`` = the phase is a guided review whose work is
    reasoning over the code; it needs the model provider.
    This is NOT the same axis as the local catalogue's engine/review/legacy
    badges, which describe *provenance* (which batch a skill shipped in).
    ``semgrep`` is badged "pre-existing" there and is an execution-plane tool
    here; typing on the badge would put a scanner on the model plane.

``requires_execution``
    The phase cannot produce a verdict without running the *target's* code.
    Under the default ``static_only`` policy these are not executed and are
    recorded ``not_applicable`` / ``policy_static_only``.

``signal``
    The target property that makes the phase meaningful (``auth``, ``mobile``,
    ``shopify``...). Empty means unconditional. The router turns detected
    signals into SELECTED / NOT_APPLICABLE once per run.

Skill bodies are resolved *by reference* to the local roots; nothing is copied
into this repository. An unresolvable name is reported ``MISSING_SKILL`` and is
never substituted by an invented body.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

PHASE_COUNT = 71

# Plane values (see module docstring).
PLANE_EXECUTION = "execution"
PLANE_MODEL = "model"

# Phase groups, purely descriptive: they label the ordinals for reporting and
# never influence ordering or selection.
GROUPS = (
    "cadrage",
    "qualite_statique",
    "validation_locale",
    "securite_coeur",
    "identite_interfaces",
    "donnees_produits",
    "supply_chain",
    "normalisation",
    "readiness",
    "verdict",
)


class PhaseSpec(NamedTuple):
    """One immutable pipeline entry. Exactly one skill."""

    ordinal: int
    skill: str
    group: str
    plane: str
    requires_execution: bool
    signal: str  # "" means unconditional

    @property
    def label(self) -> str:
        return "PHASE %02d = %s" % (self.ordinal, self.skill)


# (skill, group, plane, requires_execution, signal) in canonical order.
# The ordinal is the 1-based index in this table and is never stored twice.
_TABLE: Tuple[Tuple[str, str, str, bool, str], ...] = (
    # -- cadrage ---------------------------------------------------------- #
    ("target-inventory", "cadrage", PLANE_MODEL, False, ""),
    ("architecture-review", "cadrage", PLANE_MODEL, False, ""),
    ("audit-context-building", "cadrage", PLANE_MODEL, False, ""),
    ("audit-prep-assistant", "cadrage", PLANE_MODEL, False, ""),
    ("entry-point-analyzer", "cadrage", PLANE_MODEL, False, ""),
    ("security-threat-model", "cadrage", PLANE_MODEL, False, ""),
    # -- qualite statique ------------------------------------------------- #
    ("code-review", "qualite_statique", PLANE_MODEL, False, ""),
    ("find-bugs", "qualite_statique", PLANE_MODEL, False, ""),
    ("code-maturity-assessor", "qualite_statique", PLANE_MODEL, False, ""),
    ("differential-review", "qualite_statique", PLANE_MODEL, False, "git_diff"),
    ("coverage-analysis", "qualite_statique", PLANE_EXECUTION, True, "tests"),
    # -- validation locale (static_only by default) ----------------------- #
    ("quality-gate", "validation_locale", PLANE_EXECUTION, True, ""),
    ("qa", "validation_locale", PLANE_EXECUTION, True, ""),
    ("mutation-testing", "validation_locale", PLANE_EXECUTION, True, "tests"),
    ("property-based-testing", "validation_locale", PLANE_EXECUTION, True, "tests"),
    ("fuzz-testing", "validation_locale", PLANE_EXECUTION, True, ""),
    ("run-acceptance-tests", "validation_locale", PLANE_EXECUTION, True, "tests"),
    ("playwright", "validation_locale", PLANE_EXECUTION, True, "frontend"),
    ("performance-review", "validation_locale", PLANE_EXECUTION, True, ""),
    ("accessibility-review", "validation_locale", PLANE_EXECUTION, True, "frontend"),
    # -- securite coeur --------------------------------------------------- #
    ("semgrep", "securite_coeur", PLANE_EXECUTION, False, ""),
    ("codeql", "securite_coeur", PLANE_EXECUTION, False, ""),
    ("secret-scanner", "securite_coeur", PLANE_EXECUTION, False, ""),
    ("security-review", "securite_coeur", PLANE_MODEL, False, ""),
    ("audit-securite", "securite_coeur", PLANE_MODEL, False, ""),
    ("security-best-practices", "securite_coeur", PLANE_MODEL, False, ""),
    ("insecure-defaults", "securite_coeur", PLANE_MODEL, False, ""),
    ("variant-analysis", "securite_coeur", PLANE_MODEL, False, "confirmed_finding"),
    ("shannon", "securite_coeur", PLANE_MODEL, False, ""),
    ("fp-check", "securite_coeur", PLANE_MODEL, False, "findings"),
    ("reachability-triage", "securite_coeur", PLANE_MODEL, False, "findings"),
    ("constant-time-analysis", "securite_coeur", PLANE_MODEL, False, "crypto"),
    ("constant-time-testing", "securite_coeur", PLANE_EXECUTION, True, "crypto"),
    ("crypto-review", "securite_coeur", PLANE_MODEL, False, "crypto"),
    # -- identite et interfaces ------------------------------------------- #
    ("auth-review", "identite_interfaces", PLANE_MODEL, False, "auth"),
    ("authorization-review", "identite_interfaces", PLANE_MODEL, False, "auth"),
    ("session-security", "identite_interfaces", PLANE_MODEL, False, "auth"),
    ("account-recovery-review", "identite_interfaces", PLANE_MODEL, False, "auth"),
    ("api-security-review", "identite_interfaces", PLANE_MODEL, False, "api"),
    ("business-logic-review", "identite_interfaces", PLANE_MODEL, False, ""),
    ("client-side-security-review", "identite_interfaces", PLANE_MODEL, False, "frontend"),
    ("webhook-security-review", "identite_interfaces", PLANE_MODEL, False, "webhook"),
    # -- donnees et produits ---------------------------------------------- #
    ("data-security-review", "donnees_produits", PLANE_MODEL, False, "database"),
    ("privacy-review", "donnees_produits", PLANE_MODEL, False, "pii"),
    ("third-party-integration-review", "donnees_produits", PLANE_MODEL, False, "third_party"),
    ("generated-code-review", "donnees_produits", PLANE_MODEL, False, "generated_code"),
    ("tenant-isolation-review", "donnees_produits", PLANE_MODEL, False, "multitenant"),
    ("billing-entitlement-review", "donnees_produits", PLANE_MODEL, False, "payment"),
    ("commerce-security-review", "donnees_produits", PLANE_MODEL, False, "commerce"),
    ("shopify-integration-review", "donnees_produits", PLANE_MODEL, False, "shopify"),
    ("mobile-security-review", "donnees_produits", PLANE_MODEL, False, "mobile"),
    ("app-store-compliance", "donnees_produits", PLANE_MODEL, False, "mobile"),
    ("cloud-runtime-review", "donnees_produits", PLANE_MODEL, False, "cloud"),
    ("ai-security-review", "donnees_produits", PLANE_MODEL, False, "ai"),
    # -- supply chain et infrastructure ----------------------------------- #
    ("dependency-vuln-scan", "supply_chain", PLANE_EXECUTION, False, "dependencies"),
    ("supply-chain-risk-auditor", "supply_chain", PLANE_MODEL, False, "dependencies"),
    ("sbom-generator", "supply_chain", PLANE_EXECUTION, False, "dependencies"),
    ("license-audit", "supply_chain", PLANE_EXECUTION, False, ""),
    ("iac-security", "supply_chain", PLANE_EXECUTION, False, "iac"),
    ("gha-security-review", "supply_chain", PLANE_EXECUTION, False, "github_actions"),
    ("secure-workflow-guide", "supply_chain", PLANE_MODEL, False, "github_actions"),
    ("security-ownership-map", "supply_chain", PLANE_MODEL, False, "git"),
    # -- normalisation ---------------------------------------------------- #
    ("sarif-parsing", "normalisation", PLANE_EXECUTION, False, ""),
    ("findings-consolidator", "normalisation", PLANE_EXECUTION, False, ""),
    ("remediation-advisor", "normalisation", PLANE_MODEL, False, ""),
    # -- readiness -------------------------------------------------------- #
    ("operational-readiness", "readiness", PLANE_MODEL, False, ""),
    ("open-source-readiness", "readiness", PLANE_MODEL, False, ""),
    ("compliance-scope-review", "readiness", PLANE_MODEL, False, ""),
    # -- verdict ---------------------------------------------------------- #
    ("retest-findings", "verdict", PLANE_MODEL, False, "findings"),
    ("release-readiness", "verdict", PLANE_MODEL, False, ""),
    ("second-opinion", "verdict", PLANE_MODEL, False, ""),
)

ORDINALS: Tuple[PhaseSpec, ...] = tuple(
    PhaseSpec(i, skill, group, plane, needs_exec, signal)
    for i, (skill, group, plane, needs_exec, signal) in enumerate(_TABLE, start=1)
)

# Import-time invariants. A malformed table must fail loudly at import, not
# halfway through a run: a pipeline that silently lost a phase is worse than
# one that refuses to start.
if len(ORDINALS) != PHASE_COUNT:  # pragma: no cover - guarded by tests
    raise RuntimeError("pipeline must hold exactly %d phases, got %d" % (PHASE_COUNT, len(ORDINALS)))
if len({p.skill for p in ORDINALS}) != PHASE_COUNT:  # pragma: no cover
    raise RuntimeError("a skill appears in two phases; one skill = one phase")
if [p.ordinal for p in ORDINALS] != list(range(1, PHASE_COUNT + 1)):  # pragma: no cover
    raise RuntimeError("ordinals must be the contiguous range 1..%d" % PHASE_COUNT)
if any(p.group not in GROUPS for p in ORDINALS):  # pragma: no cover
    raise RuntimeError("unknown phase group in the pipeline table")

_BY_SKILL: Dict[str, PhaseSpec] = {p.skill: p for p in ORDINALS}
_BY_ORDINAL: Dict[int, PhaseSpec] = {p.ordinal: p for p in ORDINALS}

# Skills that need an explicit licence confirmation before they may run.
LICENSE_GATED = frozenset({"codeql"})

MISSING_SKILL = "MISSING_SKILL"


def by_ordinal(ordinal: int) -> PhaseSpec:
    try:
        return _BY_ORDINAL[int(ordinal)]
    except (KeyError, TypeError, ValueError):
        raise KeyError("no phase with ordinal %r (valid: 1..%d)" % (ordinal, PHASE_COUNT))


def by_skill(skill: str) -> PhaseSpec:
    try:
        return _BY_SKILL[skill]
    except KeyError:
        raise KeyError("no phase for skill %r" % skill)


def _env_roots() -> List[Path]:
    """Extra skill roots from ``PHASES_SKILL_ROOTS`` (os.pathsep separated).

    Exists so tests -- and users whose skills live elsewhere -- can point the
    resolver at a directory without editing code or copying skill bodies.
    """
    raw = os.environ.get("PHASES_SKILL_ROOTS", "")
    return [Path(p).expanduser() for p in raw.split(os.pathsep) if p.strip()]


def skill_roots(extra: Optional[Sequence[Path]] = None) -> List[Path]:
    """Local directories that may hold skill bodies, in priority order.

    Resolution is *by reference*: this repository never vendors a third-party
    ``SKILL.md``. Copying 71 externally-licensed skill bodies into an
    Apache-2.0 tree would be a licence problem, not a convenience.
    """
    roots: List[Path] = [Path(p) for p in (extra or [])]
    roots.extend(_env_roots())
    home = Path.home()
    roots.append(home / ".claude" / "skills")
    # Plugin-provided skills live under a per-plugin ``skills/`` directory.
    cache = home / ".claude" / "plugins" / "cache"
    if cache.is_dir():
        for entry in sorted(cache.rglob("skills")):
            if entry.is_dir():
                roots.append(entry)
    # Preserve order, drop duplicates.
    seen = set()
    unique: List[Path] = []
    for r in roots:
        key = os.path.normcase(str(r))
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def resolve_skill(skill: str, roots: Sequence[Path]) -> Optional[Path]:
    """Absolute path of ``skill``'s real ``SKILL.md``, or None if absent.

    Never falls back to a similarly-named skill: a near-miss body would make
    the run report an analysis that the requested skill did not perform.
    """
    for root in roots:
        candidate = Path(root) / skill / "SKILL.md"
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            # An unreadable root is not fatal; the next root may hold the skill.
            continue
    return None


class Resolution(NamedTuple):
    spec: PhaseSpec
    path: Optional[Path]  # None => MISSING_SKILL

    @property
    def missing(self) -> bool:
        return self.path is None


def resolve_all(roots: Optional[Sequence[Path]] = None) -> List[Resolution]:
    """Resolve all 71 ordinals, in order. Length is always ``PHASE_COUNT``."""
    search = list(roots) if roots is not None else skill_roots()
    return [Resolution(spec, resolve_skill(spec.skill, search)) for spec in ORDINALS]


def pipeline_manifest(roots: Optional[Sequence[Path]] = None) -> Dict:
    """Machine-readable proof of the ``PHASE N -> skill`` mapping.

    This is the artifact the run publishes so the 1:1 mapping can be checked
    without reading any code.
    """
    resolutions = resolve_all(roots)
    return {
        "schema": "phases-oss/audit-pipeline/1",
        "phase_count": PHASE_COUNT,
        "phases": [
            {
                "ordinal": r.spec.ordinal,
                "skill": r.spec.skill,
                "group": r.spec.group,
                "plane": r.spec.plane,
                "requires_execution": r.spec.requires_execution,
                "signal": r.spec.signal or None,
                "license_gated": r.spec.skill in LICENSE_GATED,
                "skill_path": str(r.path) if r.path else MISSING_SKILL,
            }
            for r in resolutions
        ],
        "missing": [r.spec.skill for r in resolutions if r.missing],
    }
