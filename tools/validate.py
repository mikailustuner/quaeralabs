"""Repo doğrulayıcısı. Kurallar src/quaera/contracts.py içinde.

Kullanım: uv run python tools/validate.py
"""

import sys

from quaera.contracts import main

if __name__ == "__main__":
    sys.exit(main())
