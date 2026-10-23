"""
mcp_config.py — which MCP servers run, and how the safety rules reach them.

TWO JOBS

  1. server_specs(): the servers to launch, each GATED so the system degrades
     instead of breaking. No GitHub token -> no GitHub server. No `npx` ->
     no Node servers, and the agent falls back to lib/ function tools and
     run_python. A server that will not start is simply absent from the tool
     list; it is never the reason a turn fails.

  2. The safety classification of MCP tool CALLS. safety.py scans run_python's
     source with an AST; an MCP tool call is a name plus a JSON argument blob,
     so the same rules - deletion is refused, money is refused, a few actions
     ask first - are applied here over the arguments instead. The filesystem
     server has no delete tool at all (verified: read/write/edit/move/search),
     so deletion cannot arrive by that door; the risk is a desktop server that
     can run PowerShell, and that is where the argument scan earns its place.
"""
from __future__ import annotations

import os
import shutil

import safety

WORKSPACE = str(safety.WORKSPACE)


# ─────────────────────────────────────────────────────────────────────────────
# Which sub-agent gets which servers. A sub-agent only ever sees the tools of
# the servers named here, which keeps each one's schema small and its job clear.
# ─────────────────────────────────────────────────────────────────────────────

AGENT_SERVERS = {
    "files":   ["filesystem"],
    "browser": ["playwright"],
    "desktop": ["desktop"],
    "coder":   ["filesystem", "github"],
}


def server_specs() -> list[dict]:
    """Every server worth trying to start on this machine, right now.

    Gated on what is actually present: the token for GitHub, `npx` for the
    Node servers. A spec that is not returned is a capability the agent will
    not be offered, which is the honest state when its prerequisite is missing."""
    specs: list[dict] = []
    npx = bool(shutil.which("npx") or shutil.which("npx.cmd"))

    if npx:
        # Filesystem, jailed to the workspace by handing it only that one
        # directory. It has no delete tool, so writes stay inside and nothing
        # it offers can permanently remove a file.
        specs.append({
            "name": "filesystem",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", WORKSPACE],
        })
        # Playwright drives a REAL Chrome over CDP. browser.py keeps Jarvis's
        # own profile on port 9222 (never the owner's - Chrome 136+ refuses a
        # debug port on the default profile); the browser sub-agent makes sure
        # that port is open before this attaches to it.
        specs.append({
            "name": "playwright",
            "command": "npx",
            "args": ["-y", "@playwright/mcp@latest",
                     "--cdp-endpoint", "http://127.0.0.1:9222"],
        })

    # GitHub only if a token exists. The server with no token answers nothing
    # useful and only clutters the tool list, so it is not started.
    gh_token = (os.environ.get("GITHUB_TOKEN")
                or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN") or "").strip()
    if npx and gh_token:
        specs.append({
            "name": "github",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": gh_token},
        })

    # Windows desktop control. The command differs by how it was installed, so
    # the first one found on PATH wins; if none is present the desktop
    # sub-agent falls back to lib/desktop + run_python, which is an honest
    # subset (open apps, media keys, clipboard, process list) rather than a
    # crash.
    if shutil.which("windows-mcp"):
        specs.append({"name": "desktop", "command": "windows-mcp", "args": []})
    elif shutil.which("uvx"):
        specs.append({"name": "desktop", "command": "uvx",
                      "args": ["windows-mcp"]})

    return specs


# ─────────────────────────────────────────────────────────────────────────────
# Safety over an MCP call. The argument blob is the thing under suspicion.
# ─────────────────────────────────────────────────────────────────────────────

# Tools whose very name is an irreversible or outward action. Deletion is
# refused outright wherever it appears; the rest ask first, matching the
# owner's "ask before messaging people, installing software, or killing apps".
_REFUSE_NAME = ("delete", "remove", "rmdir", "unlink", "destroy", "erase",
                "permanently")
_CONFIRM_NAME = ("kill", "terminate", "end_process", "stop_process",
                 "install", "uninstall", "send_email", "send_message",
                 "send_mail", "create_issue", "create_pull_request", "push")

# Argument text that means deletion or money no matter which tool carries it -
# the realistic route is a desktop server running a shell string.
_REFUSE_TEXT = ("rm -rf", "rmdir /s", "del /f", "del /q", "remove-item",
                "format ", "rd /s", "shutil.rmtree", "os.remove", "os.unlink")


def classify(server: str, tool: str, arguments: dict) -> tuple[str, str]:
    """(verdict, reason). verdict is 'allow', 'confirm', or 'refuse'.

    Deletion and money never reach 'confirm' - a prompt is the thing that gets
    approved, and these have no approval path (AGENTS.md 4.3a). Everything
    ordinary returns 'allow' with no friction, which is the common case."""
    name = (tool or "").lower()
    blob = _flatten(arguments).lower()

    # Money first - no override, any transport (safety.money_check's rule).
    for w in ("payment", "transfer money", "send money", "checkout", "purchase",
              "card number", "cvv", "upi", "wire transfer", "bank transfer"):
        if w in blob:
            return "refuse", "that involves money or a payment, which I never do"

    # Deletion - refused, not held.
    if any(w in name for w in _REFUSE_NAME) or any(w in blob for w in _REFUSE_TEXT):
        return ("refuse",
                "that would delete something, which I do not do - I can move it "
                "aside instead")

    if any(w in name for w in _CONFIRM_NAME):
        return "confirm", f"{tool} acts outside this machine or on a running app"

    return "allow", ""


def _flatten(obj) -> str:
    """Every string anywhere in the arguments, concatenated, for scanning."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return " ".join(_flatten(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return " ".join(_flatten(v) for v in obj)
    return str(obj)
