"""İzin kontrolü (ADR 0002, 0003, 0005).

İzinler prompt'ta değil burada, kodda zorlanır. Varsayılan her şey yasaktır;
yalnızca agents/*.yaml içinde açıkça verilen izinler geçerlidir.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

# İnsanın doğrudan yazabileceği nesne türleri (sorular, onay revizyonları, itirazlar, mesajlar).
HUMAN_WRITABLE = {"question", "hypothesis", "experiment", "critique", "message"}


class PermissionDenied(Exception):
    pass


@dataclass
class Permissions:
    agents: dict[str, dict]   # rol -> ajan tanımı
    skills: dict[str, dict]   # skill id -> tanım

    @classmethod
    def load(cls, agents_dir: Path = ROOT / "agents", skills_dir: Path = ROOT / "skills") -> "Permissions":
        agents = {}
        for p in sorted(agents_dir.glob("*.yaml")):
            spec = yaml.safe_load(p.read_text(encoding="utf-8"))
            agents[spec["role"]] = spec
        skills = {p.stem: yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(skills_dir.glob("*.yaml"))}
        return cls(agents, skills)

    def spec(self, role: str) -> dict:
        if role not in self.agents:
            raise PermissionDenied(f"undefined role: {role}")
        return self.agents[role]

    def check_graph_write(self, actor: dict, type_: str, message_kind: str | None = None) -> None:
        if actor["kind"] == "human":
            if type_ not in HUMAN_WRITABLE:
                raise PermissionDenied(f"a human cannot write a '{type_}' object; that is the agents' job")
            return
        perms = self.spec(actor["role"])["permissions"]
        if type_ == "message":
            if message_kind and message_kind not in perms["sendMessages"]:
                raise PermissionDenied(f"{actor['role']} cannot send '{message_kind}' messages")
            return
        if type_ not in perms["graphWrite"]:
            raise PermissionDenied(f"{actor['role']} '{type_}' nesnesi yazamaz (izinli: {perms['graphWrite']})")

    def allowed_tools(self, role: str) -> set[str]:
        return {t["name"] for sid in self.spec(role)["skills"] for t in self.skills[sid]["tools"]}

    def check_tool(self, role: str, tool: str) -> None:
        if tool not in self.allowed_tools(role):
            raise PermissionDenied(f"{role} cannot use the '{tool}' tool; it is not in its skills")

    def check_action(self, role: str, action: str) -> None:
        """Ajanlara hiçbir koşulda verilmeyen eylemler."""
        perms = self.spec(role)["permissions"]
        if action == "publish" and not perms["publish"]:
            raise PermissionDenied("no agent can publish; only a human")
        if action == "raise_budget_cap" and not perms["raiseBudgetCap"]:
            raise PermissionDenied("no agent can raise the budget cap; only a human")
        if action == "gpu_spend" and perms["gpuSpend"] == "none":
            raise PermissionDenied(f"{role} GPU harcayamaz")
