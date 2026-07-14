# Quickstart: a phase from start to finish

This walks through one bounded phase with the CLI. Everything is local; nothing
is published.

## 0. Install

```bash
pip install -e .
```

## 1. Declare the phase

```bash
python -m phases_oss.phases init \
  --objective "add a slugify() helper" \
  --files src/text.py --files tests/test_text.py \
  --proof "python -m pytest tests/test_text.py" \
  --level 1
```

This writes `.claude/phase-state.json` in `pending_approval`. A trivial proof
(`exit 0`, `true`) is rejected.

## 2. Approve

```bash
python -m phases_oss.phases approve
```

Now only the files in `--files` may be edited (the PreToolUse hook enforces this
when the hooks are installed; otherwise it is your discipline to respect it).

## 3. Write code, then prove

Write `src/text.py` and `tests/test_text.py`, then:

```bash
python -m phases_oss.phases prove
```

`prove` runs the proof command and records its exit code. A non-zero exit
increments an attempt counter; three failures with no progress is your signal to
change strategy.

## 4. Audit

Levels 1–3 require exactly one independent review report (any file containing
a `VERDICT` line, at least 200 bytes):

```bash
python -m phases_oss.phases audit --report .claude/phase-reviews/r1.md
```

Level 2 and above also require `--full-suite` at init (the proof must cover
the whole test suite). Level 3 additionally requires a recorded runtime proof
(`runtime --report ...`) and an explicit human validation
(`human-approve --validator <name>`) before close.

## 5. Close on a commit

```bash
git add -A && git commit -m "add slugify()"
python -m phases_oss.phases close \
  --lesson "slug collapses repeated separators" \
  --commit-sha "$(git rev-parse HEAD)"
```

`close` re-runs the proof against the *committed* tree in a throwaway worktree.
If it fails there, close is refused — the commit must actually be green. (This
re-run only happens when `--commit-sha` is given; without it close records
nothing to verify against, so always pass the sha.)

## 6. Inspect

```bash
python -m phases_oss.phases status         # current phase as JSON
cat .claude/phase-log.jsonl                # one v2 event per transition (reconstructible)
```

## Wiring the hooks (optional)

```bash
python -m phases_oss.install /path/to/this-project --apply
```

Then the PreToolUse / Stop / UserPromptSubmit hooks enforce the file scope and
the close gate from inside the agent harness. Remember: this is discipline, not
a security boundary — see the README's threat model.
