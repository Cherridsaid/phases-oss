"""Stdio client that drives the bundled Multidim server for the /phases engine.

The coupling between ``/phases`` and Multidim is **over stdio**: this client
spawns the autonomous server (``python -m phases_oss.multidim``) as a
subprocess, speaks the MCP handshake, and calls ``multidim_analyze`` in the v2
frame format. It never imports the server internals to bypass the protocol, so
the boundary stays honest and the server can evolve independently.

``prepare`` runs an analysis and persists the returned frame as an artifact
under ``<root>/.phases/analysis/<id>.json``, returning the metadata the phase
gate needs (context, depth, axes, ``analysis_ref``). That closes the last gap:
a user of the public /phases package can generate a real Multidim analysis
without any separately installed MCP server.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

# A frame_id is a server-provided identifier used to build the artifact FILE
# NAME. Constrain it to a safe grammar (same class the phase gate accepts) so a
# malicious or buggy value like '../escaped' can never escape the store dir.
_SAFE_FRAME_ID = re.compile(r"^[A-Za-z0-9._-]+$")

PROTOCOL_VERSION = "2025-06-18"
DEFAULT_TIMEOUT_S = 60.0

# Depth required per risk level, mirroring the phase engine's own mapping so a
# prepared analysis always matches what ``init --require-analysis`` will accept.
LEVEL_DEPTH = {0: "core", 1: "core", 2: "deep", 3: "full"}


class MultidimClientError(RuntimeError):
    """The bundled server was unreachable, errored, or returned no usable frame.

    Fail-closed: callers must treat this as 'no analysis produced', never as an
    empty-but-valid analysis.
    """


def _server_command() -> List[str]:
    return [sys.executable, "-m", "phases_oss.multidim"]


def _exchange(requests: Sequence[Dict], timeout: float) -> List[Dict]:
    """Spawn the server, send a full MCP handshake plus ``requests`` (one JSON
    per line), and return the parsed responses. Raises on a non-zero exit or a
    dead server."""
    handshake = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "phases-oss", "version": "1.0.0"},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n"
                      for r in list(handshake) + list(requests))
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # Ensure the child can import phases_oss even from an in-tree (non-installed)
    # checkout: prepend the package root (the dir containing 'phases_oss') to
    # PYTHONPATH. When the package is pip-installed this is a harmless no-op.
    pkg_root = str(Path(__file__).resolve().parents[2])
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = pkg_root + (os.pathsep + existing if existing else "")
    try:
        proc = subprocess.run(
            _server_command(), input=payload, capture_output=True, text=True,
            encoding="utf-8", env=env, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MultidimClientError("could not run the Multidim server: %s" % exc)
    if proc.returncode != 0:
        raise MultidimClientError(
            "Multidim server exited with %d: %s" % (proc.returncode, proc.stderr.strip()))
    responses: List[Dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            responses.append(json.loads(line))
        except (ValueError, TypeError):
            # ONLY JSON-RPC is expected on stdout; a non-JSON line is a protocol
            # breach, not something to silently skip
            raise MultidimClientError("Multidim server emitted a non-JSON line on stdout")
    return responses


def _tool_text(responses: List[Dict], call_id) -> str:
    for resp in responses:
        if not isinstance(resp, dict):
            # a non-object on the response stream is a protocol breach
            raise MultidimClientError("Multidim server sent a non-object JSON-RPC response")
        if resp.get("id") == call_id:
            result = resp.get("result")
            if not isinstance(result, dict):
                raise MultidimClientError("tool call %r returned no result" % call_id)
            if result.get("isError"):
                text = ""
                content = result.get("content")
                if isinstance(content, list) and content and isinstance(content[0], dict):
                    text = content[0].get("text", "")
                raise MultidimClientError("Multidim tool error: %s" % text)
            content = result.get("content")
            if isinstance(content, list) and content and isinstance(content[0], dict):
                return content[0].get("text", "")
            raise MultidimClientError("tool call %r returned malformed content" % call_id)
    raise MultidimClientError("no response for tool call %r" % call_id)


def analyze(subject: str, depth: str = "deep", context: Optional[str] = None,
            timeout: float = DEFAULT_TIMEOUT_S) -> Dict:
    """Run ``multidim_analyze`` in v2 format and return the parsed frame.

    Raises :class:`MultidimClientError` (fail-closed) if the server is
    unreachable or returns anything but a well-formed v2 frame.
    """
    if not isinstance(subject, str) or not subject.strip():
        raise MultidimClientError("subject is required")
    if depth not in ("core", "deep", "full"):
        raise MultidimClientError("depth must be core, deep or full")
    args: Dict = {"subject": subject, "depth": depth, "format": "v2"}
    if context:
        args["context"] = context
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "multidim_analyze", "arguments": args}}
    responses = _exchange([call], timeout)
    text = _tool_text(responses, 1)
    try:
        frame = json.loads(text)
    except (ValueError, TypeError):
        raise MultidimClientError("Multidim returned a non-JSON frame")
    if not isinstance(frame, dict) or frame.get("analysis_schema_version") != 2:
        raise MultidimClientError("Multidim did not return a v2 frame")
    if not frame.get("frame_hash"):
        raise MultidimClientError("Multidim frame is missing its frame_hash")
    # axes must be a non-empty list with at least one STRING-named axis:
    # otherwise prepare() would emit an empty axes list that init
    # --require-analysis rejects later with a confusing ANALYSIS_AXES_MISSING.
    # The name check matches prepare()'s extraction exactly (non-empty str), so
    # the two never disagree (e.g. name=0 must fail HERE, not silently vanish).
    axes = frame.get("axes")
    if (not isinstance(axes, list) or not axes
            or not any(_axis_name(a) for a in axes)):
        raise MultidimClientError("Multidim frame has no string-named axes")
    return frame


def _axis_name(axis) -> str:
    """The axis name if it is a non-empty string, else ''. Single source of
    truth shared by analyze() (validation) and prepare() (extraction)."""
    if isinstance(axis, dict):
        name = axis.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return ""


def _artifact_store_dir(root: Path) -> Path:
    return root / ".phases" / "analysis"


def prepare(subject: str, level: int, context: Optional[str] = None,
            root: Optional[Path] = None, timeout: float = DEFAULT_TIMEOUT_S) -> Dict:
    """Produce a Multidim analysis for a phase and persist it as an artifact.

    Returns the metadata the phase gate consumes:
    ``{context, depth, axes, analysis_ref, store_schema, artifact_path}``.
    The depth is derived from the risk level so it always matches what
    ``init --require-analysis`` expects. Fail-closed on any server problem.
    """
    if level not in LEVEL_DEPTH:
        raise MultidimClientError("level must be 0, 1, 2 or 3")
    depth = LEVEL_DEPTH[level]
    frame = analyze(subject, depth=depth, context=context, timeout=timeout)
    # the returned frame MUST actually be at the requested depth: otherwise we
    # would announce (say) 'full' while the analysis is only 'core', and the
    # gate would accept an insufficient analysis for the risk level
    if frame.get("depth") != depth:
        raise MultidimClientError(
            "Multidim returned depth %r, expected %r for level %d"
            % (frame.get("depth"), depth, level))
    frame_id = str(frame.get("frame_id") or "")
    # the frame_id becomes a file name AND the artifact://<store>/<id> ref:
    # reject anything outside the safe grammar (no separators, no '..')
    if not frame_id or not _SAFE_FRAME_ID.match(frame_id):
        raise MultidimClientError(
            "Multidim frame_id is missing or unsafe: %r" % frame_id)
    # validate EVERYTHING before any write, so a rejected frame never leaves an
    # orphan artifact file on disk
    try:
        store_schema = int(frame.get("store_version", 2))
    except (ValueError, TypeError, OverflowError):
        # OverflowError: int() of an infinite JSON float (1e999)
        raise MultidimClientError(
            "Multidim frame has a non-numeric store_version: %r"
            % frame.get("store_version"))
    axes = [n for n in (_axis_name(a) for a in frame.get("axes", [])) if n]
    ctx = frame.get("context") if isinstance(frame.get("context"), dict) else {}
    ctx_name = ctx.get("name")
    if not isinstance(ctx_name, str) or not ctx_name.strip():
        # fall back to a forced context, else fail-closed: init would later call
        # .strip() on a non-string context name and crash outside the contract
        ctx_name = context.strip() if isinstance(context, str) and context.strip() else ""
        if not ctx_name:
            raise MultidimClientError("Multidim frame has no string context name")
    root = (Path(root) if root is not None else Path.cwd()).resolve()
    store_dir = _artifact_store_dir(root).resolve()
    # the store dir must stay UNDER the repo root: a symlinked .phases/analysis
    # pointing elsewhere would otherwise let the artifact land outside the repo
    if root != store_dir and root not in store_dir.parents:
        raise MultidimClientError("refusing to use an analysis store outside the repo root")
    artifact_path = (store_dir / (frame_id + ".json")).resolve()
    # defence in depth: the resolved file path must stay inside the store dir
    if store_dir not in artifact_path.parents:
        raise MultidimClientError("refusing to write the artifact outside the store dir")
    try:
        store_dir.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(frame, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    except OSError as exc:
        raise MultidimClientError("could not persist the analysis artifact: %s" % exc)
    return {
        "context": ctx_name,
        "depth": depth,
        "axes": axes,
        "analysis_ref": "artifact://multidim/" + frame_id,
        "store_schema": store_schema,
        "artifact_path": str(artifact_path),
    }
