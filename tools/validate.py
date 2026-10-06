"""Repo validator. The rules live in src/quaera/contracts.py.

Usage: uv run python tools/validate.py
"""

import sys

from quaera.contracts import main

if __name__ == "__main__":
    sys.exit(main())
