"""Stop hook: refuse to "finish" while a phase is still open.

If an active phase has not both passed its proof and recorded its audit, the
agent should not stop. This turns "I'm done" into a checkpoint: run the proof,
record the audit, close the phase. Fails OPEN (allows the stop) when there is no
active phase.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from .common import active_phase, sanitize


def decide(payload: dict, start: Path) -> Optional[dict]:
    # The harness sets stop_hook_active when the agent is already continuing
    # because this hook blocked once. Blocking again would loop forever, so the
    # second stop is allowed: one nudge, never a cage (fail open, as everywhere).
    if payload.get("stop_hook_active"):
        return None
    _state_path, phase = active_phase(start)
    if phase is None:
        return None
    if phase.get("proof_passed") and phase.get("audit_passed"):
        return None

    missing = []
    if not phase.get("proof_passed"):
        missing.append("proof not passed")
    if not phase.get("audit_passed"):
        missing.append("audit not recorded")
    return {
        "decision": "block",
        "reason": (
            "objective-lock: phase '%s' is still open (%s). Run the proof and "
            "record the audit, then close the phase, before stopping."
            % (sanitize(phase.get("objective")), ", ".join(missing))
        ),
    }


def main(argv=None) -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return 0
    decision = decide(payload, Path.cwd())
    if decision is not None:
        sys.stdout.write(json.dumps(decision))
    return 0


if __name__ == "__main__":
    sys.exit(main())
