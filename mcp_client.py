"""
mcp_client.py — talk to MCP servers over stdio, with no SDK and no new deps.

WHY HAND-WRITTEN AND NOT THE `mcp` PACKAGE

    The OpenAI Agents SDK (0.24) does not import on Python 3.11 - it needs 3.12
    typing - and this machine is pinned to 3.11 because the audio stack's wheels
    are (AGENTS.md "Deviations"). Installing `mcp` 2.3 to get its client also
    dragged websockets to 16 and starlette to 1.9, which broke the phone
    (realtime.py speaks the websockets 13 API) and the FastAPI backend. Both
    were verified broken, then restored.

    The MCP stdio wire protocol is small: JSON-RPC 2.0, one JSON object per line,
    on the server's stdin/stdout. `initialize` -> `notifications/initialized` ->
    `tools/list` -> `tools/call`. Proven against @modelcontextprotocol/
    server-filesystem before this file was written. So the client is ~150 lines
    we own and can audit, instead of a dependency tree that fights the one the
    phone needs.

HOW IT FAILS

    Never into the caller. A dead server, a timeout, a server that was never
    installed - each comes back as a dict the agent can read and route around,
    exactly like run_python. An MCP server that will not start must degrade to
    "that capability is unavailable", never take a turn down with it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
START_TIMEOUT_S = 90.0          # first npx/uvx run fetches the package
CALL_TIMEOUT_S = 60.0


class MCPServer:
    """One MCP server process, spoken to over stdio. Thread-safe."""

    def __init__(self, name: str, command: str, args: list[str],
                 env: dict | None = None, cwd: str | None = None):
        self.name = name
        self.command = command
        self.args = list(args)
        self.env = env or {}
        self.cwd = cwd
        self.proc: subprocess.Popen | None = None
        self.tools: list[dict] = []
        self.alive = False
        self.error = ""                      # why it is not usable, if it is not
        self._id = 0
        self._pending: dict[int, dict] = {}  # id -> {"event":Event, "msg":dict}
        self._lock = threading.Lock()
        self._stderr_tail: list[str] = []

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Spawn and complete the handshake. Returns whether it is usable."""
        exe = shutil.which(self.command) or shutil.which(self.command + ".cmd")
        if not exe:
            self.error = (f"'{self.command}' is not on PATH, so the {self.name} "
                          f"server cannot start")
            return False
        try:
            self.proc = subprocess.Popen(
                [exe, *self.args],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", bufsize=1,
                cwd=self.cwd,
                env={**os.environ, **self.env},
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            self.error = f"could not launch {self.name}: {type(e).__name__}: {e}"
            return False

        threading.Thread(target=self._read_loop, daemon=True,
                         name=f"mcp-{self.name}").start()
        threading.Thread(target=self._drain_stderr, daemon=True,
                         name=f"mcp-{self.name}-err").start()

        init = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "jarvis", "version": "1"},
        }, timeout=START_TIMEOUT_S)
        if not init or "result" not in init:
            self.error = (f"{self.name} did not complete initialize"
                          + (f": {self._stderr_tail[-1]}" if self._stderr_tail
                             else ""))
            self.stop()
            return False

        self._notify("notifications/initialized")
        listed = self._request("tools/list", {}, timeout=30)
        self.tools = (((listed or {}).get("result") or {}).get("tools")) or []
        self.alive = True
        return True

    def stop(self) -> None:
        self.alive = False
        p = self.proc
        if p is None:
            return
        try:
            import psutil
            parent = psutil.Process(p.pid)
            for c in parent.children(recursive=True):
                c.kill()
            parent.kill()
        except Exception:
            try:
                p.terminate()
            except Exception:
                pass
        self.proc = None

    # ── calls ────────────────────────────────────────────────────────────────

    def call_tool(self, tool: str, arguments: dict | None = None,
                  timeout: float = CALL_TIMEOUT_S) -> dict:
        """Invoke one tool. Returns {ok, text, structured?, error?} - never raises."""
        if not self.alive:
            return {"ok": False, "error": self.error or f"{self.name} is not running"}
        resp = self._request("tools/call",
                             {"name": tool, "arguments": arguments or {}},
                             timeout=timeout)
        if resp is None:
            return {"ok": False, "error": f"{self.name}.{tool} timed out"}
        if "error" in resp:
            return {"ok": False, "error": str(resp["error"].get("message")
                                              or resp["error"])}
        result = resp.get("result") or {}
        # MCP returns content as a list of blocks; flatten text for the model.
        text = "\n".join(b.get("text", "") for b in (result.get("content") or [])
                         if b.get("type") == "text").strip()
        out = {"ok": not result.get("isError", False), "text": text}
        if result.get("structuredContent"):
            out["structured"] = result["structuredContent"]
        if result.get("isError"):
            out["error"] = text or "tool reported an error"
        return out

    # ── the wire ───────────────────────────────────────────────────────────

    def _request(self, method: str, params: dict, timeout: float) -> dict | None:
        if self.proc is None or self.proc.stdin is None:
            return None
        with self._lock:
            self._id += 1
            rid = self._id
            slot = {"event": threading.Event(), "msg": None}
            self._pending[rid] = slot
        payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        try:
            with self._lock:
                self.proc.stdin.write(json.dumps(payload) + "\n")
                self.proc.stdin.flush()
        except Exception as e:
            self.error = f"write to {self.name} failed: {type(e).__name__}"
            self.alive = False
            return None
        if not slot["event"].wait(timeout):
            self._pending.pop(rid, None)
            return None
        return slot["msg"]

    def _notify(self, method: str, params: dict | None = None) -> None:
        if self.proc is None or self.proc.stdin is None:
            return
        try:
            with self._lock:
                self.proc.stdin.write(json.dumps(
                    {"jsonrpc": "2.0", "method": method,
                     "params": params or {}}) + "\n")
                self.proc.stdin.flush()
        except Exception:
            pass

    def _read_loop(self) -> None:
        """Route every response to whoever is waiting on its id."""
        p = self.proc
        if p is None or p.stdout is None:
            return
        for line in p.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            rid = msg.get("id")
            if rid is None:
                continue                     # a notification or log, not a reply
            slot = self._pending.pop(rid, None)
            if slot is not None:
                slot["msg"] = msg
                slot["event"].set()
        self.alive = False                   # stdout closed: the server exited

    def _drain_stderr(self) -> None:
        p = self.proc
        if p is None or p.stderr is None:
            return
        for line in p.stderr:
            line = line.rstrip()
            if line:
                self._stderr_tail.append(line)
                del self._stderr_tail[:-20]


# ─────────────────────────────────────────────────────────────────────────────
# The manager — start configured servers once, address their tools uniformly
# ─────────────────────────────────────────────────────────────────────────────

class MCPManager:
    """Holds the running servers and a flat tool index across all of them."""

    def __init__(self) -> None:
        self.servers: dict[str, MCPServer] = {}
        self._tool_owner: dict[str, str] = {}   # tool name -> server name
        self._lock = threading.Lock()

    def start(self, specs: list[dict]) -> dict:
        """Start every spec. Returns a report; a failure is recorded, not raised.

        A spec is {name, command, args, env?, cwd?}. Servers that fail to start
        are simply absent from the tool index - the agent sees fewer tools, not
        a crash."""
        report = {}
        for spec in specs:
            name = spec["name"]
            srv = MCPServer(name, spec["command"], spec.get("args") or [],
                            spec.get("env"), spec.get("cwd"))
            ok = srv.start()
            with self._lock:
                self.servers[name] = srv
                if ok:
                    for t in srv.tools:
                        self._tool_owner.setdefault(t["name"], name)
            report[name] = {"ok": ok, "tools": len(srv.tools),
                            "error": srv.error}
        return report

    def tools_for(self, server_names: list[str]) -> list[dict]:
        """Every tool exposed by the named servers, as MCP tool descriptors."""
        out = []
        for n in server_names:
            srv = self.servers.get(n)
            if srv and srv.alive:
                out.extend(srv.tools)
        return out

    def call(self, tool: str, arguments: dict | None = None,
             timeout: float = CALL_TIMEOUT_S) -> dict:
        """Call a tool by name, routing to whichever server owns it."""
        owner = self._tool_owner.get(tool)
        if owner is None:
            return {"ok": False, "error": f"no MCP server offers a tool named "
                                          f"{tool!r}"}
        return self.servers[owner].call_tool(tool, arguments, timeout)

    def owner_of(self, tool: str) -> str | None:
        return self._tool_owner.get(tool)

    def stop_all(self) -> None:
        for srv in self.servers.values():
            srv.stop()


_MANAGER: MCPManager | None = None


def manager() -> MCPManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = MCPManager()
    return _MANAGER
