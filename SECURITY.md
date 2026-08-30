# Security policy

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Report it privately through [GitHub's private vulnerability
reporting](https://github.com/Cherridsaid/phases-oss/security/advisories/new),
or by email to **cherridsaid@gmail.com** with `phases-oss security` in the
subject line.

Please include what you need to make the problem reproducible: the version, the
platform, the steps, and what you expected instead. A proof of concept helps;
run it only against your own machine.

You can expect an acknowledgement within seven days, and an assessment within
thirty. This is a project maintained by one person on their own time — the
delay is honest rather than optimistic.

Coordinated disclosure: please give a fix a reasonable window before publishing.
Credit is given in the advisory unless you ask otherwise.

## Supported versions

Only the latest published version receives fixes.

| Version | Supported |
|---------|-----------|
| 0.1.x   | yes       |

## What is, and is not, a vulnerability here

Read [the threat model](README.md#honest-threat-model--read-this-first) first —
it is deliberately the first substantive section of the README.

The local tooling is **a discipline aid, not a security boundary.** An agent and
its reviewer run on the same machine with the same rights. No local lock — a
hook, a secret, a hash — can stop a determined process on that machine from
editing the state file and lifting every restriction. The hooks fail open by
design, so that they never wedge an unrelated session.

So the following are **known and documented**, not vulnerabilities:

- a local process editing `.claude/phase-state.json` to lift a gate;
- a hook being bypassed by a process that does not call it;
- network isolation in the audit pipeline being **advisory**: proxy variables
  point at a closed port, which stops well-behaved HTTP clients, but there is no
  per-process network namespace, so a raw socket is not blocked. The run reports
  `advisory` and never claims to be offline.

These, on the other hand, are worth reporting:

- a secret reaching a subprocess, a log, a journal or an outbound payload
  despite the scrubbing and the data gate;
- the audit pipeline writing to, or executing code from, the target repository
  under its read-only default;
- a command built by the tool reaching the network when it says it will not
  (for example a rule registry fetched mid-scan);
- a review verdict being treated as a pass when it is not — a strict-parsing
  escape;
- anything that lets a phase close without its proof having actually passed.
