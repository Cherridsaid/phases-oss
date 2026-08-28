"""Execution-plane tools: what each deterministic phase actually runs.

One entry per execution-plane ordinal. A phase whose tool is not installed ends
``skipped_offline`` / ``tool_absent`` -- it is never silently replaced by a model
writing what the tool "would have" reported.

Nothing here downloads anything. Rule packs, vulnerability databases and CodeQL
packs must already be on disk; every command carries the flags that disable
telemetry and auto-update, and :data:`FORBIDDEN_FLAGS` is asserted by a test so
a future edit cannot quietly add ``--update`` back.

Two phases are served by this package's own code rather than an external binary
(``sarif-parsing``, ``findings-consolidator``); they are always available.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence

from .sarif import KIND_REPORT, KIND_SARIF, KIND_SBOM

#: Flags that would make a "local, offline" run reach the network. Asserted.
FORBIDDEN_FLAGS = (
    "--update", "--auto-update", "--download", "--refresh",
    "--verified", "--online", "--metrics=on",
)

INTERNAL = "<internal>"


class ToolSpec(NamedTuple):
    """How to invoke one execution-plane phase."""

    executable: str
    args: Sequence[str]
    artifact_kind: str
    output_flag: Optional[str] = None  # flag carrying the output path, if any
    alternatives: Sequence[str] = ()   # equivalent executables, tried in order

    @property
    def internal(self) -> bool:
        return self.executable == INTERNAL


#: skill -> ToolSpec. Placeholders ``{target}`` and ``{out}`` are substituted by
#: :func:`build_command`; no shell is involved, so no quoting hazard.
TOOLS: Dict[str, ToolSpec] = {
    "semgrep": ToolSpec(
        "semgrep",
        ("--metrics=off", "--disable-version-check", "--sarif", "--output", "{out}",
         "--config", "{rules}", "{target}"),
        KIND_SARIF,
    ),
    # No pack download, no database build: CodeQL only runs against a database
    # the user already created, behind an explicit licence confirmation.
    "codeql": ToolSpec(
        "codeql",
        ("database", "analyze", "{database}", "--format=sarifv2.1.0",
         "--output={out}", "--offline"),
        KIND_SARIF,
    ),
    "secret-scanner": ToolSpec(
        "gitleaks",
        ("detect", "--source", "{target}", "--no-banner", "--redact",
         "--report-format", "sarif", "--report-path", "{out}"),
        KIND_SARIF,
        alternatives=("trufflehog",),
    ),
    "dependency-vuln-scan": ToolSpec(
        "osv-scanner",
        ("--offline", "--format", "sarif", "--output", "{out}", "-r", "{target}"),
        KIND_SARIF,
        alternatives=("grype", "trivy"),
    ),
    "sbom-generator": ToolSpec(
        "syft",
        ("scan", "dir:{target}", "-o", "cyclonedx-json={out}"),
        KIND_SBOM,
        alternatives=("cdxgen", "trivy"),
    ),
    "license-audit": ToolSpec(
        # Consumes the SBOM produced by PHASE 57 rather than re-walking the tree.
        INTERNAL, ("consume-sbom",), KIND_REPORT,
    ),
    "iac-security": ToolSpec(
        "checkov",
        ("--directory", "{target}", "--output", "sarif", "--output-file-path", "{out}",
         "--skip-download"),
        KIND_SARIF,
        alternatives=("tfsec", "trivy"),
    ),
    "gha-security-review": ToolSpec(
        "zizmor",
        ("--offline", "--format", "sarif", "{target}"),
        KIND_SARIF,
        alternatives=("actionlint",),
    ),
    "coverage-analysis": ToolSpec("coverage", ("report", "--format=markdown"), KIND_REPORT),
    "quality-gate": ToolSpec(INTERNAL, ("project-test-suite",), KIND_REPORT),
    "qa": ToolSpec(INTERNAL, ("project-test-suite",), KIND_REPORT),
    "mutation-testing": ToolSpec("mutmut", ("run",), KIND_REPORT, alternatives=("cosmic-ray",)),
    "property-based-testing": ToolSpec("hypothesis", ("--version",), KIND_REPORT),
    "fuzz-testing": ToolSpec("atheris", ("--version",), KIND_REPORT, alternatives=("afl-fuzz",)),
    "run-acceptance-tests": ToolSpec(INTERNAL, ("project-test-suite",), KIND_REPORT),
    "playwright": ToolSpec("playwright", ("test",), KIND_REPORT),
    "performance-review": ToolSpec("lighthouse", ("--output=json", "--output-path={out}"), KIND_REPORT),
    "accessibility-review": ToolSpec("axe", ("--save", "{out}"), KIND_REPORT),
    "constant-time-testing": ToolSpec("dudect", ("{target}",), KIND_REPORT),
    "sarif-parsing": ToolSpec(INTERNAL, ("parse-sarif",), KIND_REPORT),
    "findings-consolidator": ToolSpec(INTERNAL, ("consolidate",), KIND_REPORT),
}


def _assert_no_forbidden_flags() -> None:
    """Import-time guard: no command may carry a network-reaching flag."""
    for skill, spec in TOOLS.items():
        for arg in spec.args:
            for bad in FORBIDDEN_FLAGS:
                if arg == bad or arg.startswith(bad + "="):
                    raise RuntimeError(
                        "tool %r carries forbidden flag %r (the run must not "
                        "download or phone home)" % (skill, arg)
                    )


_assert_no_forbidden_flags()


#: Where a local semgrep rule pack may live, in priority order.
SEMGREP_RULES_ENV = "PHASES_SEMGREP_RULES"
_SEMGREP_RULE_DIRS = (
    Path.home() / ".shannon-tools" / "rules" / "semgrep-rules",
    Path.home() / ".semgrep" / "semgrep-rules",
    Path.home() / ".config" / "semgrep" / "rules",
)

#: semgrep prints its rule accounting on stderr; parsed, never guessed.
_RULES_LOADED = re.compile(r"(?im)^\s*(?:scanning .*? with |Loaded )?(\d+)\s+(?:Code )?rules?\b")
_RULES_FAILED = re.compile(r"(?im)^\s*(\d+)\s+rules?\s+(?:failed|errored|skipped)")


def semgrep_rules() -> Optional[Path]:
    """First local semgrep rule pack found, or None.

    ``--config auto`` is never an option: it downloads the registry at scan
    time, which is exactly what an offline execution plane must not do. With no
    local pack the phase is reported ``tool_absent`` rather than quietly
    reaching the network.
    """
    override = os.environ.get(SEMGREP_RULES_ENV, "").strip()
    candidates = ([Path(override).expanduser()] if override else []) + list(_SEMGREP_RULE_DIRS)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def rule_stats(rules_dir: Optional[Path], output: str = "") -> Dict[str, Optional[int]]:
    """Rule accounting for the report: discovered from disk, loaded from output.

    ``rules_discovered`` counts the pack's rule files. ``rules_loaded`` and
    ``rules_failed`` are parsed from semgrep's own output and stay ``None`` when
    it did not say -- an invented number would be worse than an absent one. No
    count is ever hardcoded.
    """
    discovered = None
    if rules_dir is not None and Path(rules_dir).is_dir():
        discovered = sum(1 for _ in Path(rules_dir).rglob("*.y*ml"))
    loaded = _RULES_LOADED.search(output or "")
    failed = _RULES_FAILED.search(output or "")
    return {
        "rules_discovered": discovered,
        "rules_loaded": int(loaded.group(1)) if loaded else None,
        "rules_failed": int(failed.group(1)) if failed else None,
    }


def resolve_executable(spec: ToolSpec) -> Optional[str]:
    """First installed executable among the primary and its alternatives."""
    if spec.internal:
        return INTERNAL
    for candidate in (spec.executable, *spec.alternatives):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def is_available(skill: str) -> bool:
    spec = TOOLS.get(skill)
    return spec is not None and resolve_executable(spec) is not None


def build_command(
    skill: str,
    *,
    target: Path,
    out: Path,
    rules: Optional[Path] = None,
    database: Optional[Path] = None,
) -> List[str]:
    """Full argv for ``skill``. Raises KeyError when the skill has no tool."""
    spec = TOOLS[skill]
    executable = resolve_executable(spec)
    if executable is None:
        raise FileNotFoundError("no executable installed for %r" % skill)
    if "{rules}" in " ".join(spec.args) and rules is None:
        # No silent "auto": that flag downloads the registry mid-scan.
        raise FileNotFoundError(
            "%r needs a local rule pack; none found (set %s)" % (skill, SEMGREP_RULES_ENV)
        )
    substitutions = {
        "{target}": str(target),
        "{out}": str(out),
        "{rules}": str(rules) if rules else "",
        "{database}": str(database) if database else "",
    }
    argv = [executable]
    for arg in spec.args:
        for placeholder, value in substitutions.items():
            arg = arg.replace(placeholder, value)
        argv.append(arg)
    return argv


def availability_report() -> Dict[str, Dict]:
    """Which execution-plane tools this machine can actually run."""
    return {
        skill: {
            "primary": spec.executable,
            "alternatives": list(spec.alternatives),
            "resolved": resolve_executable(spec),
            "available": resolve_executable(spec) is not None,
            "artifact_kind": spec.artifact_kind,
        }
        for skill, spec in sorted(TOOLS.items())
    }
