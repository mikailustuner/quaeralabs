"""Where QuaeraLabs finds its files, in a repo checkout and in an installed wheel (improvement plan A3).

The rule files (agents/, skills/, schemas/) and the built web UI are shipped inside the wheel under `quaera/_data/`;
a repo checkout reads them from the repo. Large environments live outside both: the ML environment and the Lean
workspace default to the repo (install.sh) and, for an installed package, to ~/.quaera (`quaera setup ml`).
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parents[1]
IN_REPO = (REPO / "pyproject.toml").exists() and (REPO / "agents").is_dir()


def quaera_home() -> Path:
    return Path(os.environ.get("QUAERA_HOME", Path.home() / ".quaera"))


def data_path(*parts: str) -> Path:
    """A shipped file or directory: the wheel's copy if present, otherwise the repo's."""
    packaged = PACKAGE.joinpath("_data", *parts)
    return packaged if packaged.exists() else REPO.joinpath(*parts)


def ml_env() -> Path:
    if os.environ.get("QUAERA_ML_ENV"):
        return Path(os.environ["QUAERA_ML_ENV"])
    return REPO / "ml-env" if IN_REPO else quaera_home() / "ml-env"


def lean_workspace() -> Path:
    if os.environ.get("QUAERA_LEAN_WORKSPACE"):
        return Path(os.environ["QUAERA_LEAN_WORKSPACE"])
    return REPO / "lean" if IN_REPO else quaera_home() / "lean"
