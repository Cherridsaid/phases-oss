"""Test package for phases-oss.

Importing this package makes the in-tree ``src/`` layout importable without an
editable install, so ``python -m unittest discover -s tests`` works from a clean
checkout with no build step.
"""

import os
import sys

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
