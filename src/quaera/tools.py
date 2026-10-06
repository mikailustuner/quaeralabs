"""Tool layer (ADR 0006).

Agents reach tools only through this registry. Before every call, the role's skills are
checked for that tool (permissions are enforced in code). MCP tools are called over persistent
stdio sessions; every call is written to the event log.

Tool name mapping: `lean.compile` in a skill (server `lean`) → MCP tool `lean_compile`.
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
    """Keeps MCP sessions open on a background event loop."""

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
            self.permissions.check_action(role, "gpu_spend")  # GPU only for permitted roles
        server = self.server_of.get(tool)
        if server not in SERVERS:
            raise ToolError(f"the server ({server}) for the '{tool}' tool is not available in this version")
        # Live view: long-running tools (Lean compilation, sandbox experiments) are also logged when they start; the code
        # being compiled or written (at most 20k characters) is shown in the UI's "what is happening now" panel.
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
