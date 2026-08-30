# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` and this changelog.

## [0.1.0] — 2026-08-30

First published release.

### Added

- **Phase engine.** Phases declare an objective, the files they may touch, a
  proof command and a risk level (`0`–`3`). A phase moves through explicit
  gates: `init → approve → prove → audit → [review] → [human-approve] → close`.
  The proof command's exit code is the single source of truth.
- **Independent close verification.** `close` re-runs the proof against the
  *committed* tree in a throwaway git worktree, so a green working copy cannot
  pass for a green commit.
- **Reconstructible journal.** Every transition appends a v2 event to
  `.claude/phase-log.jsonl`, carrying a full state snapshot — the journal alone
  can rebuild the phase.
- **Reviewers.** `local` is a static, offline, regex-based linter with no model
  and no network by construction. `cloud` is a thin shell around a sender you
  wire yourself; with no sender it is inert, and every payload passes a data
  gate first — allowlisted destination, redacted content, disclosure attached.
  It fails closed: an absent backend, an unreachable sender, an empty or
  unparseable reply all yield `REVIEW_UNAVAILABLE`, never a pass.
- **Three hooks** porting the gates into an agent harness: `PreToolUse`, `Stop`,
  `UserPromptSubmit`.
- **Multidim**, a structured multi-axis analysis engine with its own stdio MCP
  server and its own store, never fused into the phase engine.
- **`phases-audit`**, a frozen 71-phase audit pipeline, one skill per phase. A
  phase that does not apply is still visited and receives a terminal status and
  a typed reason from a closed vocabulary.
- **Package safety gate** (`tests/test_package_safety.py`): the build fails if a
  `SKILL.md`, an embedded Semgrep rule pack, a third-party tool binary — caught
  by name *and* by executable magic number, so a renamed scanner is still
  caught — vendored code, or a runtime dependency ever enters the tree.
- **Bilingual README**, English and French, cross-linked.
- **Publishing by OIDC** (PyPI Trusted Publishing). No API token exists in the
  repository, in the secrets, or on a maintainer's machine.

### Notes

- Zero runtime dependencies; standard library only.
- Python 3.9+ — the range the CI actually proves, on Ubuntu and Windows across
  3.9, 3.11 and 3.13.
- The local tooling is a discipline aid, not a security boundary. See
  [the threat model](README.md#honest-threat-model--read-this-first).

[Unreleased]: https://github.com/Cherridsaid/phases-oss/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Cherridsaid/phases-oss/releases/tag/v0.1.0
