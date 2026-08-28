"""``phases-audit`` command line.

    phases-audit pipeline            print the frozen PHASE N -> skill mapping
    phases-audit tools               which execution-plane tools are installed
    phases-audit run --target DIR    visit all 71 phases
    phases-audit resume RUN_ID       continue an interrupted run at its ordinal
    phases-audit status RUN_ID       the end-of-run report

Nothing here publishes: no remote is added, nothing is pushed, no release is
made. ``open-source-readiness`` and ``release-readiness`` return a verdict and
stop there.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from . import tools
from .registry import PHASE_COUNT, pipeline_manifest
from .runner import (
    POLICY_LOCAL_EXECUTION,
    POLICY_STATIC_ONLY,
    AuditRunner,
    report,
)
from .runstate import RunState, RunStateError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="phases-audit",
        description="Deterministic %d-phase audit pipeline (one skill per phase)." % PHASE_COUNT,
    )
    parser.add_argument("--root", default=".", help="where run artifacts are written (default: cwd)")
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("pipeline", help="print the frozen ordinal -> skill mapping")
    sub.add_parser("tools", help="report execution-plane tool availability")

    p_run = sub.add_parser("run", help="run the pipeline over a target")
    p_run.add_argument("--target", required=True, help="directory to audit (never modified)")
    p_run.add_argument(
        "--allow-local-test-execution",
        action="store_true",
        help="run the target's own code in an ephemeral copy (default: static only)",
    )
    p_run.add_argument(
        "--enable-codeql",
        action="store_true",
        help="confirm the CodeQL licence terms; without it PHASE 22 is skipped_license",
    )
    p_run.add_argument("--skill-root", action="append", default=[],
                       help="extra directory holding skill bodies (repeatable)")

    p_resume = sub.add_parser("resume", help="continue an interrupted run")
    p_resume.add_argument("run_id")
    p_resume.add_argument("--target", default=None, help="override the recorded target")

    p_status = sub.add_parser("status", help="print a run's report")
    p_status.add_argument("run_id")
    return parser


def _print(payload) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()

    try:
        if args.action == "pipeline":
            _print(pipeline_manifest())
            return 0

        if args.action == "tools":
            _print(tools.availability_report())
            return 0

        if args.action == "run":
            target = Path(args.target).resolve()
            if not target.is_dir():
                print("AUDIT_ERROR: target is not a directory: %s" % target, file=sys.stderr)
                return 2
            roots: List[Path] = [Path(p) for p in args.skill_root]
            runner = AuditRunner(
                target,
                root=root,
                policy=POLICY_LOCAL_EXECUTION if args.allow_local_test_execution else POLICY_STATIC_ONLY,
                codeql_license_confirmed=args.enable_codeql,
                skill_roots=roots or None,
            )
            state = runner.run()
            _print(report(state))
            return 0

        if args.action == "resume":
            state = RunState.load(root, args.run_id)
            target = Path(args.target or state.data["target"]).resolve()
            runner = AuditRunner(target, root=root, policy=state.data["policy"],
                                 codeql_license_confirmed=state.data.get("codeql_license_confirmed", False))
            state = runner.run(resume_from=args.run_id)
            _print(report(state))
            return 0

        if args.action == "status":
            _print(report(RunState.load(root, args.run_id)))
            return 0
    except RunStateError as exc:
        print("AUDIT_ERROR: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
