"""Araç katmanı (ADR 0006).

Ajanlar araçlara yalnızca bu kayıt üzerinden erişir. Her çağrıdan önce rolün skill'lerinde
o aracın bulunup bulunmadığı kontrol edilir (izinler kodda zorlanır). MCP araçları kalıcı
stdio oturumlarıyla çağrılır; her çağrı olay kaydına yazılır.

Araç adı eşlemesi: skill'deki `lean.compile` (sunucu `lean`) → MCP aracı `lean_compile`.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from contextlib import AsyncExitStack
from typing import Callable

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .permissions import Permissions

SERVERS = {"lean": "quaera.mcp_servers.lean_server", "literature": "quaera.mcp_servers.literature_server",
           "sandbox": "quaera.mcp_servers.sandbox_server"}


class ToolError(Exception):
    pass


class MCPPool:
    """Arka plandaki bir olay döngüsünde MCP oturumlarını açık tutar."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.stack: AsyncExitStack | None = None
        self.sessions: dict[str, ClientSession] = {}

    def _run(self, coro, timeout: float):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _session(self, server: str) -> ClientSession:
        if server not in self.sessions:
            if self.stack is None:
                self.stack = AsyncExitStack()
            params = StdioServerParameters(command=sys.executable, args=["-m", SERVERS[server]], env=dict(os.environ))
            read, write = await self.stack.enter_async_context(stdio_client(params))
            session = await self.stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self.sessions[server] = session
        return self.sessions[server]

    async def _call(self, server: str, tool: str, args: dict) -> str:
        session = await self._session(server)
        result = await session.call_tool(tool, args)
        text = "".join(getattr(c, "text", "") for c in result.content)
        if result.is_error:
            raise ToolError(text)
        return text

    def call(self, server: str, tool: str, args: dict, timeout: float = 3600) -> str:
        return self._run(self._call(server, tool, args), timeout)

    def close(self) -> None:
        if self.stack is not None:
            try:
                self._run(self.stack.aclose(), 30)
            except Exception:
                pass
        self.loop.call_soon_threadsafe(self.loop.stop)


class ToolRegistry:
    def __init__(self, permissions: Permissions, record: Callable[[str, dict], None] = lambda k, p: None,
                 pool: MCPPool | None = None):
        self.permissions = permissions
        self.record = record
        self.pool = pool or MCPPool()
        self.server_of = {t["name"]: t.get("server") for s in permissions.skills.values() for t in s["tools"]
                          if t["protocol"] == "mcp"}

    def call(self, role: str, tool: str, **args) -> str:
        self.permissions.check_tool(role, tool)
        if tool == "sandbox.exec" and args.get("gpu"):
            self.permissions.check_action(role, "gpu_spend")  # GPU yalnızca izinli rollerde
        server = self.server_of.get(tool)
        if server not in SERVERS:
            raise ToolError(f"the server ({server}) for the '{tool}' tool is not available in this version")
        # Canlı görünüm: uzun süren araçlar (Lean derlemesi, sandbox'ta deney) başlarken de kayda geçer; derlenen ya da
        # yazılan kod (en fazla 20 bin karakter) arayüzde "şu an ne yapılıyor" panelinde gösterilir.
        code = args.get("source") or args.get("content")
        self.record("tool.started", {"role": role, "tool": tool,
                                     "args": {k: (v if len(str(v)) < 200 else str(v)[:200] + "…") for k, v in args.items()
                                              if k not in ("source", "content")},
                                     **({"code": str(code)[:20000]} if code else {})})
        out = self.pool.call(server, tool.replace(".", "_"), args)
        self.record("tool.call", {"role": role, "tool": tool, "args": {k: (v if len(str(v)) < 200 else str(v)[:200] + "…")
                                                                        for k, v in args.items()},
                                  "resultPreview": out[:300]})
        return out

    def call_json(self, role: str, tool: str, **args):
        return json.loads(self.call(role, tool, **args))

    def close(self) -> None:
        self.pool.close()
