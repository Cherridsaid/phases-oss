"""Applicability routing: which phases have something to look at.

The router answers one question per ordinal -- *does this phase's subject exist
in the target?* -- and never touches the order. Its output is frozen into the
run state once (:meth:`RunState.freeze_applicability`) and is the run's fixed
truth afterwards.

Detection is deterministic and file-based: filenames, extensions and a bounded
keyword pass over text files. No model is consulted, so two runs over the same
tree produce the same matrix.

Two signals are *runtime* rather than structural: ``findings`` and
``confirmed_finding`` describe what earlier phases produced, not what the target
contains. They stay SELECTED in the matrix and are resolved when the phase runs
(``not_applicable`` / ``no_findings_to_process`` when nothing was found). Baking
them into the frozen matrix would either skip them before the scanners ran or
force a matrix rewrite mid-run, and the matrix must not be rewritten.

Known limitation, stated rather than hidden: a keyword scan over-detects (the
word "checkout" in a comment marks ``commerce``) and under-detects (a minified
bundle hides everything). Over-detection costs a phase that finds nothing;
under-detection silently drops one. That asymmetry is why every ``signal_absent``
decision is written to the report with the signal name attached, so a wrong
detection is visible instead of invisible.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .registry import ORDINALS, PhaseSpec

SELECTED = "SELECTED"
NOT_APPLICABLE = "NOT_APPLICABLE"

#: Signals resolved while the run progresses, not from the target's files.
RUNTIME_SIGNALS = frozenset({"findings", "confirmed_finding"})

#: Directories never worth walking; they dwarf the real source tree.
_SKIP_DIRS = frozenset(
    {
        ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
        "env", ".env", "dist", "build", ".next", ".nuxt", "target", "vendor",
        ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "coverage",
        "htmlcov", ".gradle", "Pods", ".idea", ".vscode", "site-packages",
    }
)

_TEXT_SUFFIXES = frozenset(
    {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte",
        ".go", ".rs", ".rb", ".php", ".java", ".kt", ".swift", ".cs", ".c",
        ".cc", ".cpp", ".h", ".hpp", ".sh", ".ps1", ".sql", ".graphql",
        ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env", ".tf",
        ".html", ".htm", ".md", ".txt", ".xml", ".plist", ".gradle", ".dart",
    }
)

_MAX_FILES = 4000
_MAX_BYTES = 512 * 1024

# signal -> (filename patterns, path fragments, content keywords)
_RULES: Tuple[Tuple[str, Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]], ...] = (
    ("tests", ("test_*.py", "*_test.py", "*.test.js", "*.test.ts", "*.spec.ts", "*.spec.js"),
     ("tests/", "test/", "__tests__/", "spec/"), ()),
    ("dependencies",
     ("package.json", "requirements.txt", "pyproject.toml", "go.mod", "Cargo.toml",
      "Gemfile", "pom.xml", "build.gradle", "composer.json", "pubspec.yaml"), (), ()),
    ("github_actions", (), (".github/workflows/",), ()),
    ("iac", ("*.tf", "*.tfvars", "serverless.yml", "cloudformation.yaml"),
     ("terraform/", "k8s/", "kubernetes/", "helm/"), ()),
    ("cloud", ("Dockerfile", "docker-compose.yml", "docker-compose.yaml",
               "wrangler.toml", "vercel.json", "fly.toml", "Procfile"), (), ()),
    ("frontend", ("index.html", "*.jsx", "*.tsx", "*.vue", "*.svelte", "*.css", "*.scss"),
     ("public/", "static/",), ("react", "vue", "svelte", "document.queryselector")),
    ("api", ("openapi.yaml", "openapi.json", "swagger.json", "*.graphql"), ("routes/", "api/"),
     ("fastapi", "@app.route", "express()", "router.get", "apollo", "graphql",
      "@restcontroller", "http.handlefunc")),
    ("auth", (), (),
     ("login", "signin", "sign_in", "authenticate", "jwt", "oauth", "session_id",
      "password_hash", "bcrypt", "argon2", "set-cookie", "refresh_token")),
    ("multitenant", (), (),
     ("tenant_id", "tenant-id", "organization_id", "org_id", "workspace_id",
      "row level security", "row_level_security")),
    ("payment", (), (),
     ("stripe", "paypal", "subscription", "invoice", "billing", "entitlement",
      "price_id", "checkout.session")),
    ("commerce", (), (),
     ("add_to_cart", "addtocart", "checkout", "order_total", "line_items",
      "inventory", "refund")),
    ("shopify", ("shopify.app.toml",), (), ("shopify", "myshopify.com", "x-shopify")),
    ("mobile", ("AndroidManifest.xml", "Info.plist", "pubspec.yaml", "Podfile"),
     ("android/", "ios/"), ("expo", "react-native")),
    ("ai", (), (),
     ("openai", "anthropic", "langchain", "llama_index", "embedding", "vector store",
      "system prompt", "chat.completions", "claude-", "gpt-4", "rag")),
    ("crypto", (), (),
     ("hashlib", "hmac", "aes", "rsa", "ed25519", "secp256k1", "cipher", "encrypt(",
      "decrypt(", "sign(", "private_key", "keypair")),
    ("webhook", (), (), ("webhook", "x-hub-signature", "x-signature", "svix")),
    ("database", ("*.sql", "schema.prisma", "models.py", "alembic.ini"),
     ("migrations/",), ("select ", "insert into", "supabase", "postgres", "mongodb",
                        "sqlalchemy", "sequelize")),
    ("pii", (), (),
     ("email", "phone_number", "date_of_birth", "gdpr", "rgpd", "personal data",
      "national_id", "ssn", "address_line")),
    ("third_party", (), (), ("api_key", "sdk", "client_secret", "https://api.")),
    ("generated_code", (), (), ("lovable", "generated by v0", "bolt.new", "base44")),
)


def _keyword_pattern(keyword: str) -> "re.Pattern[str]":
    """Compile a content keyword with boundaries, so it matches a whole token.

    A bare substring test is wrong in a way that is easy to miss: ``expo``
    matches ``export``, which marked every JavaScript project as a mobile app.
    Boundaries are applied only where the keyword's own edge is a word
    character, so entries carrying punctuation (``@app.route``, ``x-shopify``,
    ``select ``) keep working.
    """
    left = r"(?<![a-z0-9_])" if (keyword[0].isalnum() or keyword[0] == "_") else ""
    right = r"(?![a-z0-9_])" if (keyword[-1].isalnum() or keyword[-1] == "_") else ""
    return re.compile(left + re.escape(keyword) + right)


#: signal -> compiled keyword patterns, built once (the content pass is
#: files x keywords, so recompiling per file would dominate the walk).
_KEYWORD_PATTERNS: Dict[str, Tuple[Tuple[str, "re.Pattern[str]"], ...]] = {}


class Signals:
    """Detected target properties. Immutable once built."""

    def __init__(self, present: Iterable[str], evidence: Optional[Dict[str, str]] = None):
        self._present: Set[str] = set(present)
        self.evidence: Dict[str, str] = dict(evidence or {})

    def __contains__(self, signal: str) -> bool:
        return signal in self._present

    def as_list(self) -> List[str]:
        return sorted(self._present)


def _iter_files(target: Path) -> Iterable[Path]:
    count = 0
    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".egg")]
        for name in filenames:
            yield Path(dirpath) / name
            count += 1
            if count >= _MAX_FILES:
                return


def detect(target: Path) -> Signals:
    """Walk ``target`` once and report which signals are present."""
    target = Path(target)
    if not target.is_dir():
        raise ValueError("target is not a directory: %s" % target)

    present: Set[str] = set()
    evidence: Dict[str, str] = {}
    haystack: List[Tuple[str, str]] = []  # (lowercased content, relative path)

    if (target / ".git").exists():
        present.add("git")
        evidence["git"] = ".git"

    for path in _iter_files(target):
        try:
            rel = path.relative_to(target).as_posix()
        except ValueError:  # pragma: no cover - defensive
            continue
        rel_lower = rel.lower()
        name = path.name

        for signal, patterns, fragments, _keywords in _RULES:
            if signal in present:
                continue
            if any(Path(name).match(p) for p in patterns) or any(f in rel_lower for f in fragments):
                present.add(signal)
                evidence[signal] = rel

        if path.suffix.lower() in _TEXT_SUFFIXES:
            try:
                if path.stat().st_size > _MAX_BYTES:
                    continue
                haystack.append((path.read_text(encoding="utf-8", errors="ignore").lower(), rel))
            except OSError:
                continue

    for signal, _patterns, _fragments, keywords in _RULES:
        if signal in present or not keywords:
            continue
        compiled = _KEYWORD_PATTERNS.get(signal)
        if compiled is None:
            compiled = tuple((k, _keyword_pattern(k)) for k in keywords)
            _KEYWORD_PATTERNS[signal] = compiled
        for content, rel in haystack:
            hit = next((k for k, pattern in compiled if pattern.search(content)), None)
            if hit is not None:
                present.add(signal)
                evidence[signal] = "%s (%r)" % (rel, hit)
                break

    # A tracked repo with more than one commit can be reviewed differentially.
    if "git" in present:
        present.add("git_diff")
        evidence.setdefault("git_diff", ".git")

    return Signals(present, evidence)


def decide(spec: PhaseSpec, signals: Signals) -> Tuple[str, Optional[str]]:
    """(decision, missing_signal) for one ordinal."""
    if not spec.signal:
        return SELECTED, None
    if spec.signal in RUNTIME_SIGNALS:
        # Resolved when the phase runs, not here (see module docstring).
        return SELECTED, None
    if spec.signal in signals:
        return SELECTED, None
    return NOT_APPLICABLE, spec.signal


def build_matrix(target: Path, signals: Optional[Signals] = None) -> Dict[str, Dict]:
    """The full 71-entry applicability matrix, keyed by ordinal (as a string).

    JSON object keys are strings, so the ordinal is stored as ``"01".."71"``
    to keep the frozen matrix round-trippable through the run state without a
    key-type surprise on reload.
    """
    signals = signals if signals is not None else detect(target)
    matrix: Dict[str, Dict] = {
        "_signals": {"present": signals.as_list(), "evidence": signals.evidence},
    }
    for spec in ORDINALS:
        decision, missing = decide(spec, signals)
        matrix["%02d" % spec.ordinal] = {
            "skill": spec.skill,
            "decision": decision,
            "signal": spec.signal or None,
            "missing_signal": missing,
        }
    return matrix
