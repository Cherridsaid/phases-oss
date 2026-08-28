"""SARIF 2.1.0 as the canonical finding format, plus consolidation.

Every phase that produces *findings* produces them as SARIF. A tool whose native
output is something else is converted by a deterministic adapter; the model is
never asked to invent what a tool reported.

An SBOM is not a finding. ``sbom-generator`` emits CycloneDX/SPDX and a stage
envelope, and :func:`validate_artifact` refuses to dress it up as an empty SARIF
run -- an empty ``results`` array reads as "this tool found nothing", which is a
different and false claim.

Consolidation keeps provenance. Two tools reporting the same issue at the same
place are merged into one finding that still lists both sources, both rule ids
and both severities, and gains a ``confidence`` derived from how many
independent tools agreed. Nothing is discarded on merge.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/"
    "sarif-schema-2.1.0.json"
)

#: Artifact kinds a phase may publish. ``sarif`` is the only one carrying
#: findings; the others are inventories or evidence.
KIND_SARIF = "sarif"
KIND_SBOM = "sbom"
KIND_REPORT = "report"
ARTIFACT_KINDS = frozenset({KIND_SARIF, KIND_SBOM, KIND_REPORT})

_LEVELS = ("none", "note", "warning", "error")


class SarifError(Exception):
    """Raised when a document does not satisfy the SARIF contract."""


class Finding(NamedTuple):
    rule_id: str
    message: str
    file: str
    line: int
    level: str
    tool: str
    cwe: Tuple[str, ...] = ()

    @property
    def key(self) -> Tuple[str, str, int]:
        """Identity used for cross-tool merging: where, and what kind."""
        return (self.file.replace("\\", "/").lower(), _normalize_rule(self.rule_id), self.line)


def _normalize_rule(rule_id: str) -> str:
    """Collapse tool-specific rule namespaces so two tools can agree.

    ``python.lang.security.audit.sql-injection`` and ``py/sql-injection`` both
    reduce to ``sql-injection``. Imperfect by nature -- it is a merge heuristic,
    not a taxonomy -- which is why the merged finding keeps every original rule
    id rather than replacing them with the normalised form.
    """
    tail = str(rule_id).replace("\\", "/").split("/")[-1]
    return tail.split(".")[-1].strip().lower()


def validate_sarif(document: Dict) -> None:
    """Structural validation of a SARIF 2.1.0 log. Raises :class:`SarifError`."""
    if not isinstance(document, dict):
        raise SarifError("SARIF document is not an object")
    if document.get("version") != SARIF_VERSION:
        raise SarifError("SARIF version must be %r (got %r)" % (SARIF_VERSION, document.get("version")))
    runs = document.get("runs")
    if not isinstance(runs, list) or not runs:
        raise SarifError("SARIF document has no runs")
    for index, run in enumerate(runs):
        if not isinstance(run, dict):
            raise SarifError("run %d is not an object" % index)
        driver = ((run.get("tool") or {}).get("driver") or {})
        if not driver.get("name"):
            raise SarifError("run %d has no tool.driver.name" % index)
        results = run.get("results", [])
        if not isinstance(results, list):
            raise SarifError("run %d results is not an array" % index)
        for r_index, result in enumerate(results):
            if not isinstance(result, dict):
                raise SarifError("run %d result %d is not an object" % (index, r_index))
            if not (result.get("message") or {}).get("text"):
                raise SarifError("run %d result %d has no message.text" % (index, r_index))
            level = result.get("level", "warning")
            if level not in _LEVELS:
                raise SarifError(
                    "run %d result %d has level %r (allowed: %s)"
                    % (index, r_index, level, ", ".join(_LEVELS))
                )


def validate_artifact(path: Path, kind: str) -> None:
    """Check that ``path`` really is an artifact of ``kind``.

    The SBOM guard is the point of this function: an SBOM handed over as SARIF
    would be counted as a scanner that found nothing.
    """
    if kind not in ARTIFACT_KINDS:
        raise SarifError("unknown artifact kind %r" % kind)
    path = Path(path)
    if not path.is_file():
        raise SarifError("artifact not found: %s" % path)
    if kind == KIND_REPORT:
        return
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SarifError("artifact is not readable JSON (%s): %s" % (exc.__class__.__name__, path))
    if kind == KIND_SARIF:
        if _looks_like_sbom(document):
            raise SarifError(
                "this artifact is an SBOM, not a SARIF log: publish it as kind "
                "%r (an empty SARIF run would falsely read as 'no findings')" % KIND_SBOM
            )
        validate_sarif(document)
        return
    if not _looks_like_sbom(document):
        raise SarifError("artifact is not a CycloneDX or SPDX document: %s" % path)


def _looks_like_sbom(document: Dict) -> bool:
    if not isinstance(document, dict):
        return False
    return (
        document.get("bomFormat") == "CycloneDX"
        or "spdxVersion" in document
        or "SPDXID" in document
    )


def findings_from_sarif(document: Dict, *, tool: Optional[str] = None) -> List[Finding]:
    """Flatten a validated SARIF log into :class:`Finding` objects."""
    validate_sarif(document)
    findings: List[Finding] = []
    for run in document["runs"]:
        driver = (run.get("tool") or {}).get("driver") or {}
        tool_name = tool or driver.get("name") or "unknown"
        taxonomy = _rule_taxonomy(driver)
        for result in run.get("results", []):
            rule_id = str(result.get("ruleId") or "unknown")
            file_path, line = _first_location(result)
            findings.append(
                Finding(
                    rule_id=rule_id,
                    message=str((result.get("message") or {}).get("text", "")),
                    file=file_path,
                    line=line,
                    level=str(result.get("level", "warning")),
                    tool=str(tool_name),
                    cwe=taxonomy.get(rule_id, ()),
                )
            )
    return findings


def _rule_taxonomy(driver: Dict) -> Dict[str, Tuple[str, ...]]:
    """rule id -> CWE identifiers declared by the driver."""
    out: Dict[str, Tuple[str, ...]] = {}
    for rule in driver.get("rules", []) or []:
        if not isinstance(rule, dict):
            continue
        cwes = [
            str(tag)
            for tag in ((rule.get("properties") or {}).get("tags") or [])
            if str(tag).upper().startswith("CWE")
        ]
        if cwes:
            out[str(rule.get("id"))] = tuple(cwes)
    return out


def _first_location(result: Dict) -> Tuple[str, int]:
    for location in result.get("locations", []) or []:
        physical = (location or {}).get("physicalLocation") or {}
        artifact = (physical.get("artifactLocation") or {})
        uri = artifact.get("uri")
        if uri:
            region = physical.get("region") or {}
            try:
                line = int(region.get("startLine", 0))
            except (TypeError, ValueError):
                line = 0
            return str(uri), line
    return "", 0


class Consolidated(NamedTuple):
    rule_ids: Tuple[str, ...]
    message: str
    file: str
    line: int
    severity: str  # strictest original level, never averaged
    sources: Tuple[str, ...]
    cwe: Tuple[str, ...]
    confidence: str
    cross_confirmed: bool


def _strictest(levels: Iterable[str]) -> str:
    # Materialise first: ``levels`` is usually a generator, and rebuilding the
    # set inside the comprehension would consume it on the first probe and see
    # an empty set for every level after that -- silently downgrading a merged
    # "error" to the "warning" default.
    seen = set(levels)
    ranked = [lvl for lvl in _LEVELS if lvl in seen]
    return ranked[-1] if ranked else "warning"


def consolidate(findings: Sequence[Finding]) -> List[Consolidated]:
    """Merge duplicates across tools while preserving every source.

    Confidence is the count of *distinct tools* that independently reported the
    same place and rule family: one tool = ``low``, two = ``medium``, three or
    more = ``high``. It is a corroboration count, not a probability.
    """
    buckets: Dict[Tuple[str, str, int], List[Finding]] = {}
    for finding in findings:
        buckets.setdefault(finding.key, []).append(finding)

    out: List[Consolidated] = []
    for key in sorted(buckets):
        group = buckets[key]
        tools = tuple(sorted({f.tool for f in group}))
        confidence = "high" if len(tools) >= 3 else "medium" if len(tools) == 2 else "low"
        out.append(
            Consolidated(
                rule_ids=tuple(sorted({f.rule_id for f in group})),
                # Longest message: the most specific one carries the evidence.
                message=max((f.message for f in group), key=len, default=""),
                file=group[0].file,
                line=group[0].line,
                severity=_strictest(f.level for f in group),
                sources=tools,
                cwe=tuple(sorted({c for f in group for c in f.cwe})),
                confidence=confidence,
                cross_confirmed=len(tools) >= 2,
            )
        )
    # Strictest first, then most corroborated: the report opens on what matters.
    order = {level: rank for rank, level in enumerate(reversed(_LEVELS))}
    out.sort(key=lambda c: (order.get(c.severity, 9), -len(c.sources), c.file, c.line))
    return out


def stage_envelope(
    *,
    ordinal: int,
    skill: str,
    status: str,
    reason: str,
    artifacts: Sequence[Tuple[str, str]] = (),
    note: Optional[str] = None,
) -> Dict:
    """The per-phase envelope written next to a phase's artifacts."""
    return {
        "schema": "phases-oss/stage-envelope/1",
        "ordinal": ordinal,
        "skill": skill,
        "status": status,
        "reason": reason,
        "note": note,
        "artifacts": [{"kind": kind, "path": str(path)} for kind, path in artifacts],
    }


def empty_sarif(tool_name: str) -> Dict:
    """A syntactically valid, explicitly empty SARIF log for ``tool_name``.

    Only for a tool that genuinely ran and found nothing. Never use it to stand
    in for a tool that did not run -- that is what the run statuses are for.
    """
    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [{"tool": {"driver": {"name": tool_name}}, "results": []}],
    }
