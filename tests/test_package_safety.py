"""PACKAGE SAFETY GATE — what must never be shipped with this project.

The README makes four promises that are, in the end, promises about the
*contents of the repository*: no skill bodies are vendored, no third-party rule
pack is embedded, no scanner binary rides along, and there are no runtime
dependencies. A promise nothing enforces is a promise that rots.

This gate inspects the tree rather than a built wheel on purpose: it must run
identically on all six CI machines with the standard library alone, without the
``build`` frontend, and the tree is a superset of what ships — to PyPI through
``src/``, and to GitHub as a whole.

Every check fails loudly and names the offending path. None of them is skipped:
a gate that skips itself where the risk is highest is not a gate.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Local-only noise: build artefacts, caches, virtualenvs, editor and tooling
#: state. None of it is tracked, so none of it can be published. ``node_modules``
#: is deliberately absent — it is something we want the gate to *catch*.
IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".claude",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".swarm",
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
        ".venv",
        "venv",
        "env",
        "graphify-out",
    }
)

#: Executable image magic numbers: PE (Windows), ELF (Linux), Mach-O (macOS, both
#: endians and the fat variant). Catches a bundled scanner whatever its filename.
EXECUTABLE_MAGIC = (
    b"MZ",
    b"\x7fELF",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
)

#: Third-party scanners this project drives but must never carry.
FORBIDDEN_TOOL_NAMES = frozenset(
    {
        "semgrep",
        "semgrep-core",
        "codeql",
        "gitleaks",
        "syft",
        "grype",
        "trivy",
        "bandit",
        "nuclei",
        "subfinder",
        "httpx",
        "katana",
        "osv-scanner",
        "checkov",
        "snyk",
    }
)

#: Directory names that mean "someone else's source code lives here".
VENDORED_DIRECTORY_NAMES = frozenset(
    {"vendor", "vendored", "_vendor", "third_party", "thirdparty", "node_modules"}
)

#: A Semgrep rule declares the languages it applies to and at least one pattern.
#: Requiring both keeps ordinary YAML — CI workflows included — out of the net.
_SEMGREP_LANGUAGES = re.compile(r"^\s*-?\s*languages\s*:", re.MULTILINE)
_SEMGREP_PATTERN = re.compile(r"^\s*-?\s*patterns?\s*:", re.MULTILINE)


def _walk() -> list:
    """Every file in the tree, minus local-only noise."""
    found = []
    stack = [ROOT]
    while stack:
        current = stack.pop()
        for entry in current.iterdir():
            if entry.is_symlink():
                # Never follow a link out of the tree, but do report it.
                found.append(entry)
                continue
            if entry.is_dir():
                if entry.name in IGNORED_DIRECTORIES or entry.name.endswith(".egg-info"):
                    continue
                stack.append(entry)
            else:
                found.append(entry)
    return found


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


class PackageSafetyGate(unittest.TestCase):
    """The repository must not carry what the README says it does not carry."""

    @classmethod
    def setUpClass(cls):
        cls.files = _walk()

    def test_the_gate_actually_sees_the_project(self):
        # Guards the gate itself: a broken walk would make every other check
        # pass vacuously, which is the classic way a safety net stops existing.
        names = {_relative(path) for path in self.files}
        self.assertIn("pyproject.toml", names)
        self.assertIn("README.md", names)
        self.assertTrue(
            any(name.startswith("src/phases_oss/") for name in names),
            "the package source was not reached; the gate is inspecting nothing",
        )

    def test_no_skill_body_is_vendored(self):
        # Skill bodies are resolved by reference from the user's own roots.
        # Shipping one would silently turn a reference into a fork.
        offenders = [
            _relative(path) for path in self.files if path.name.lower() == "skill.md"
        ]
        self.assertEqual([], offenders, "SKILL.md must never be shipped: %s" % offenders)

    def test_no_third_party_semgrep_rules_are_embedded(self):
        offenders = []
        for path in self.files:
            if path.suffix.lower() not in (".yml", ".yaml"):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:  # pragma: no cover - unreadable file is not a rule pack
                continue
            if _SEMGREP_LANGUAGES.search(text) and _SEMGREP_PATTERN.search(text):
                offenders.append(_relative(path))
        self.assertEqual(
            [], offenders, "embedded Semgrep rules must stay out: %s" % offenders
        )

    def test_no_third_party_tool_binary_rides_along(self):
        offenders = []
        for path in self.files:
            stem = path.stem.lower() if path.suffix.lower() in ("", ".exe") else ""
            if stem in FORBIDDEN_TOOL_NAMES:
                offenders.append(_relative(path))
        self.assertEqual(
            [], offenders, "third-party tool binaries must stay out: %s" % offenders
        )

    def test_no_executable_image_of_any_kind(self):
        # Stronger than the name check above: a renamed scanner is still a
        # scanner. Read the magic number instead of trusting the filename.
        offenders = []
        for path in self.files:
            try:
                with path.open("rb") as handle:
                    head = handle.read(4)
            except OSError:  # pragma: no cover - unreadable file cannot be run
                continue
            if any(head.startswith(magic) for magic in EXECUTABLE_MAGIC):
                offenders.append(_relative(path))
        self.assertEqual(
            [], offenders, "compiled executables must stay out: %s" % offenders
        )

    def test_no_undeclared_vendored_code(self):
        offenders = []
        for path in self.files:
            parts = {part.lower() for part in path.relative_to(ROOT).parts[:-1]}
            hit = parts & VENDORED_DIRECTORY_NAMES
            if hit:
                offenders.append(_relative(path))
        self.assertEqual(
            [], offenders, "undeclared vendored code must stay out: %s" % offenders
        )

    def test_runtime_dependencies_stay_empty(self):
        # Parsed by hand: tomllib only exists from 3.11, and this gate must run
        # on 3.9 without adding a dependency to prove we have no dependencies.
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(
            match, "pyproject.toml no longer declares a 'dependencies' list"
        )
        body = re.sub(r"#.*", "", match.group(1)).strip()
        self.assertEqual(
            "", body, "zero dependencies is a published promise; found: %s" % body
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
