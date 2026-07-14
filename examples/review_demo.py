#!/usr/bin/env python3
"""Runnable demo: the default static reviewer, and the inert cloud reviewer.

    python examples/review_demo.py

No network, no dependencies. The cloud reviewer stays inert because no sender is
wired -- that is the whole point of the opt-in design.
"""

from phases_oss.reviewers import scan_text
from phases_oss.reviewers.base import findings_to_verdict
from phases_oss.reviewers.cloud import CloudReviewer

SAMPLE = '''\
def handler(req):
    api_key = "AKIAEXAMPLE1234567"   # hardcoded secret -> error
    try:
        return run(req, shell=True)   # shell=True -> warning
    except:                            # bare except -> warning
        breakpoint()                   # debugger -> error
    # TODO: handle timeouts           # note
'''


class _Phase:
    """Minimal stand-in for a real phase (only files_allowed is read)."""

    files_allowed = []  # the demo scans inline text instead of files


def main():
    print("=== static local reviewer (offline, no LLM) ===")
    findings = scan_text(SAMPLE, "handler.py")
    for f in findings:
        print("  " + f.render())

    verdict = findings_to_verdict(findings)
    print("  verdict:", verdict.verdict)
    if verdict.action:
        print("  action:", verdict.action)

    print()
    print("=== cloud reviewer with no sender wired (inert) ===")
    cloud = CloudReviewer()  # no sender, no endpoint
    print("  is_configured:", cloud.is_configured())
    cloud_verdict = cloud.review(_Phase())
    print("  verdict:", cloud_verdict.verdict)
    print("  notes:", cloud_verdict.notes)

    # A clean reviewer is the default; nothing left the machine.
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
