"""Deterministic 71-phase audit pipeline.

One skill, one phase, always. The ordinal order is frozen at import time and
never recomputed: a run *visits* all 71 phases, executes the applicable ones,
and records an explicit typed reason for every phase it does not execute.

Layout
------
``registry``  the 71 frozen ordinals + resolution to real local skill bodies
``router``    applicability matrix, computed once per run and then immutable
``runstate``  append-only run journal, one RUNNING phase at a time, resume
``overlay``   throwaway per-phase stage exposing exactly one SKILL.md
``guard``     provider-secret denylist and read-only target enforcement
``sarif``     SARIF 2.1.0 envelope validation (no invented tool output)
"""

from __future__ import annotations

from .registry import (
    ORDINALS,
    PHASE_COUNT,
    PhaseSpec,
    resolve_all,
    skill_roots,
)

__all__ = [
    "ORDINALS",
    "PHASE_COUNT",
    "PhaseSpec",
    "resolve_all",
    "skill_roots",
]
