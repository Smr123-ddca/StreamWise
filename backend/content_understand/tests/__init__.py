"""Test suite for the StreamWise Content Understanding Engine."""

from pathlib import Path
import sys

# Ensure repository root is importable regardless of test runner working directory.
_REPO_ROOT = str(Path(__file__).resolve().parents[3])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
