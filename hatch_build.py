"""Build hook: puts the rule files and the built web UI into a wheel under quaera/_data (improvement plan A3).

Only for a real wheel. An editable install reads them from the repo (quaera.resources.data_path), so `uv sync` works on
a fresh checkout before the UI is built."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

DATA = {"agents": "agents", "skills": "skills", "schemas": "schemas", "web/dist": "web/dist"}


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if version == "editable":
            return
        root = Path(self.root)
        if not (root / "web" / "dist" / "index.html").exists():
            raise RuntimeError("build the web UI before the wheel: cd web && npm ci && npm run build")
        for src, dst in DATA.items():
            build_data["force_include"][str(root / src)] = f"quaera/_data/{dst}"
