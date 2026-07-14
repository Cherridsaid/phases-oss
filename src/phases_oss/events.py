"""Reconstructible JSONL v2 journal shared by the engine and the reviewers.

Every phase transition appends one structured event to
``<repo>/.claude/phase-log.jsonl``. Each line carries the same required
identifiers (``phase_id``, ``session_id``, ``project_id``, ``event_id``) plus a
``payload`` that defaults to a full state snapshot, so the phase state can be
reconstructed from the journal alone. ``review_id`` / ``finding_id`` are null
outside a review. The optional ``analysis`` field carries pre-phase analysis
*metadata only* (context, depth, axes, artifact ref) -- never the analysis
text itself.

The journal is append-only and local-only (git-ignored), like the state file.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

SCHEMA_VERSION = 2

_PROJECT_ID_FILENAME = "project-id"
_PROJECT_ID_RE = re.compile(r"^prj_[0-9a-f]{32}$")


def new_id(prefix: str) -> str:
    """A fresh identifier such as ``ph_<32 hex>`` / ``evt_<32 hex>``."""
    return "%s_%s" % (prefix, uuid.uuid4().hex)


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + (
        "%03dZ" % (datetime.now(timezone.utc).microsecond // 1000)
    )


def get_or_create_project_id(state_dir: Path) -> str:
    """Stable per-project id, persisted in ``.claude/project-id``."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / _PROJECT_ID_FILENAME
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if _PROJECT_ID_RE.match(existing):
            return existing
    pid = new_id("prj")
    path.write_text(pid, encoding="utf-8")
    return pid


def ensure_ids(state: Dict, state_dir: Path) -> Dict:
    """Guarantee project/phase/session ids on ``state`` (migrates old states)."""
    if not state.get("project_id"):
        state["project_id"] = get_or_create_project_id(state_dir)
    if not state.get("phase_id"):
        state["phase_id"] = new_id("ph")
    if not state.get("session_id"):
        state["session_id"] = new_id("ses")
    state["schema_version"] = SCHEMA_VERSION
    return state


def write_event(
    state_dir: Path,
    event_type: str,
    state: Dict,
    *,
    review_id: Optional[str] = None,
    finding_id: Optional[str] = None,
    analysis: Optional[Dict] = None,
    payload: Optional[Dict] = None,
) -> Dict:
    """Append one v2 event; returns the event dict.

    ``payload`` defaults to a full state snapshot so the journal alone can
    rebuild the state. Raises ``ValueError`` when a required id is missing
    after migration -- an event without identity is worse than no event.
    """
    ensure_ids(state, state_dir)
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": new_id("evt"),
        "phase_id": state["phase_id"],
        "session_id": state["session_id"],
        "project_id": state["project_id"],
        "review_id": review_id,
        "finding_id": finding_id,
        "timestamp_utc": _timestamp_utc(),
        "event_type": event_type,
        "analysis": analysis,
        "payload": payload if payload is not None else {"state": state},
    }
    for name in ("phase_id", "session_id", "project_id"):
        if not event[name]:
            raise ValueError("jsonl v2: %s missing on event" % name)
    log_path = state_dir / "phase-log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event
