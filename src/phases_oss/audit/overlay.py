"""One skill visible at a time: a throwaway stage per phase.

Before ``PHASE 21 = semgrep`` runs, a fresh stage directory is materialised
holding exactly one skill body::

    <stage>/.claude/skills/semgrep/SKILL.md

When the phase ends the stage is destroyed and the next one is built with only
its own skill. The 71 bodies are never exposed together.

Why a HOME override
-------------------
Copying one skill into the stage is not enough: an agent process started with
the user's real ``HOME`` still discovers ``~/.claude/skills`` and would see all
397 local skills. ``stage_env`` therefore repoints ``HOME`` (and the Windows
``USERPROFILE`` / ``HOMEDRIVE`` / ``HOMEPATH`` trio) at the stage, so the stage's
``.claude`` *is* the only skill root the child can resolve.

Honest scope
------------
This is process hygiene, not a sandbox. A child that hardcodes an absolute path
to ``C:/Users/<name>/.claude/skills`` still reaches the real root. The overlay
removes the *discovery* path, which is what makes an agent pick up a skill it
was not given; it does not confine a process that goes looking on purpose.
``visible_skills`` exists so a test can assert what the stage actually exposes
rather than what it was meant to expose.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from .registry import PhaseSpec

SKILL_SUBPATH = Path(".claude") / "skills"


class OverlayError(Exception):
    """Raised when a stage cannot be built or does not hold exactly one skill."""


def _force_remove(func, path, _exc):  # pragma: no cover - platform specific
    """rmtree onerror hook: clear the read-only bit and retry once.

    Windows refuses to unlink read-only files, which is exactly what a copied
    skill body can be. A stage that fails to delete would leak the previous
    phase's skill into the next one's environment, so the removal must not be
    best-effort.
    """
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        raise


def visible_skills(stage: Path) -> List[str]:
    """Names of the skills discoverable inside ``stage``, sorted.

    Used by the invariants: a stage that exposes zero or two skills is a bug,
    not a detail.
    """
    root = Path(stage) / SKILL_SUBPATH
    if not root.is_dir():
        return []
    return sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and (entry / "SKILL.md").is_file()
    )


def stage_env(stage: Path, base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Environment whose home directory is the stage.

    Callers combine this with :func:`phases_oss.audit.guard.scrub_env`; the
    order matters only in that both must be applied before spawning anything.
    """
    env = dict(os.environ if base is None else base)
    stage = Path(stage).resolve()
    env["HOME"] = str(stage)
    env["USERPROFILE"] = str(stage)
    drive, tail = os.path.splitdrive(str(stage))
    if drive:
        env["HOMEDRIVE"] = drive
        env["HOMEPATH"] = tail or os.sep
    # The resolver reads this; leaving a stale value would re-expose the real
    # skill root that the stage exists to hide.
    env["PHASES_SKILL_ROOTS"] = str(stage / SKILL_SUBPATH)
    return env


class SkillStage:
    """Context manager materialising exactly one skill, then destroying it.

    ::

        with SkillStage(spec, skill_path) as stage:
            run_phase(stage.root, stage.env())
        # stage.root no longer exists here
    """

    def __init__(
        self,
        spec: PhaseSpec,
        skill_path: Path,
        *,
        parent: Optional[Path] = None,
    ):
        self.spec = spec
        self.skill_path = Path(skill_path)
        self._parent = Path(parent) if parent else None
        self.root: Optional[Path] = None

    # -- lifecycle -------------------------------------------------------- #
    def build(self) -> Path:
        if not self.skill_path.is_file():
            raise OverlayError(
                "skill body absent for %s: %s (never substitute another body)"
                % (self.spec.skill, self.skill_path)
            )
        parent = str(self._parent) if self._parent else None
        if parent:
            Path(parent).mkdir(parents=True, exist_ok=True)
        self.root = Path(
            tempfile.mkdtemp(prefix="phases-stage-%02d-" % self.spec.ordinal, dir=parent)
        )
        destination = self.root / SKILL_SUBPATH / self.spec.skill
        destination.mkdir(parents=True, exist_ok=True)
        source_dir = self.skill_path.parent
        # Copy the whole skill directory: SKILL.md alone is not a skill when it
        # references scripts/, references/ or schemas/. The copy lives in a temp
        # stage and is deleted with it, so no third-party body is ever vendored
        # into this repository.
        shutil.copytree(source_dir, destination, dirs_exist_ok=True)
        exposed = visible_skills(self.root)
        if exposed != [self.spec.skill]:
            self.destroy()
            raise OverlayError(
                "stage for PHASE %02d exposes %r, expected exactly [%r]"
                % (self.spec.ordinal, exposed, self.spec.skill)
            )
        return self.root

    def destroy(self) -> None:
        if self.root is not None and self.root.exists():
            shutil.rmtree(self.root, onerror=_force_remove)
        self.root = None

    def env(self, base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        if self.root is None:
            raise OverlayError("stage is not built")
        return stage_env(self.root, base)

    def __enter__(self) -> "SkillStage":
        self.build()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # Always destroy, including on failure: a surviving stage would leak the
        # failed phase's skill into the next phase's environment.
        self.destroy()
        return False
