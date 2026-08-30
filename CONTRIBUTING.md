# Contributing

Thanks for looking. This project is small and opinionated; the rules below exist
so a contribution does not get refused for a reason nobody wrote down.

## The one hard rule

**Zero runtime dependencies.** The standard library only, and no exception. A
patch that adds a dependency is refused however good it is — the promise is on
the package page, and `tests/test_package_safety.py` fails the build if the
dependency list ever stops being empty.

The same gate refuses a vendored `SKILL.md`, an embedded Semgrep rule pack, a
third-party scanner binary — by filename *and* by executable magic number — and
any vendored directory. If that gate turns red on your branch, it is telling you
something real; do not work around it.

## Before you open a pull request

```bash
python run_tests.py       # standard-library unittest, exit 0 = green
python smoke_install.py   # builds a wheel, installs it in a throwaway venv
```

Both must pass. The CI runs the suite on Ubuntu and Windows across Python 3.9,
3.11 and 3.13, so `3.9` is the floor: no syntax or standard-library feature
newer than that.

## What a good patch looks like

- **One concern per pull request.** A bug fix and a refactor in the same diff
  take three times as long to review.
- **A test that fails without your change.** Write it first and watch it fail;
  a test that passes before the fix is testing nothing.
- **No test that depends on a tool being installed**, and no `skipUnless` to
  hide it. A test skipped on five of six CI machines is a test that does not
  exist. Simulate the environment instead — `unittest.mock` is in the standard
  library.
- **Comments that say why, not what.** The code already says what.

## Reporting a bug

Open an issue with the version, the platform, the exact command, what you
expected and what happened. If you have a reproduction that fits in twenty
lines, that is worth more than a paragraph of description.

**For a security problem, do not open an issue** — see [SECURITY.md](SECURITY.md).

## Before you build something large

Open an issue first and describe it. This project refuses features more often
than it accepts them, and it would be a shame for you to find that out after
writing the code rather than before.

Worth knowing what is deliberately out of scope: this tool reads local code,
read-only. It does not exploit, scan networks, or send anything to a third-party
system. It does not ship the skills it resolves by reference. Those are not
missing features.

## Licence

By contributing you agree that your contribution is licensed under
[Apache-2.0](LICENSE), like the rest of the project.
