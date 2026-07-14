#!/usr/bin/env python3
"""Run the test suite with the standard library only.

This runner exists so the proof command is a single, portable invocation
(``python run_tests.py``) with no shell quoting and no dependency on how
``unittest`` discovery is launched:

* it puts the in-tree ``src/`` layout on ``sys.path`` (no editable install
  needed), so tests can ``import phases_oss`` from a clean checkout;
* it writes all test output to **stdout** (not stderr), so callers that treat
  any stderr byte as a failure still see a green run as success;
* the process exit code is the single source of truth (0 = all passed).
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)


def main():
    loader = unittest.defaultTestLoader
    suite = loader.discover(start_dir="tests", top_level_dir=ROOT, pattern="test_*.py")
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
