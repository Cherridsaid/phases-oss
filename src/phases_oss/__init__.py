"""phases-oss: a phase-based workflow runner for AI coding agents.

The package provides a small, dependency-free toolkit to drive disciplined,
phase-based work:

* a state machine (``phases.py``) with explicit gates: init, approve, prove,
  audit, review, close;
* hooks that enforce the active phase's file scope and gate completion;
* a default *static* reviewer (regular expressions only, no LLM) plus an
  opt-in cloud reviewer shell guarded by a data gate.

The local tooling is a discipline aid, not a security boundary. See the README
for the honest threat model: the real authority is deterministic tests, CI, and
human review.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
