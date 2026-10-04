#!/usr/bin/env python3
"""Register codex-cu and its skill with each harness, or remove them.

Each harness keeps its own MCP configuration; this uses the harness CLI where
one exists and edits Cursor's JSON directly. The proxy is registered by its
absolute path in this checkout, so `git pull` updates every harness at once.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROXY = ROOT / "codex_cu_mcp.py"
SKILL = ROOT / "skills/codex-cu"
NAME = "codex-cu"
HOME = Path.home()
ENGINE = HOME / ".codex/plugins/cache/openai-bundled/unified-computer-use"
CURSOR_CONFIG = HOME / ".cursor/mcp.json"

# Skill directory each harness reads; Cursor and Grok share ~/.agents/skills.
SKILL_DIRS = {
    "claude": HOME / ".claude/skills",
    "devin": HOME / ".config/devin/skills",
    "grok": HOME / ".agents/skills",
    "cursor": HOME / ".agents/skills",
}
CLI_ADD = {
    "claude": ["claude", "mcp", "add", "-s", "user", NAME, "--", str(PROXY)],
    "devin": ["devin", "mcp", "add", "-s", "user", NAME, "--", str(PROXY)],
    "grok": ["grok", "mcp", "add", "-s", "user", NAME, str(PROXY)],
}
CLI_REMOVE = {
    "claude": ["claude", "mcp", "remove", "-s", "user", NAME],
    "devin": ["devin", "mcp", "remove", "-s", "user", NAME],
    "grok": ["grok", "mcp", "remove", "-s", "user", NAME],
}


def run(argv, check=True):
    result = subprocess.run(argv, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SystemExit(f"{' '.join(argv[:3])} failed: {(result.stderr or result.stdout).strip()}")
    return result


def edit_cursor(add):
    config = json.loads(CURSOR_CONFIG.read_text()) if CURSOR_CONFIG.exists() else {}
    servers = config.setdefault("mcpServers", {})
    if add:
        servers[NAME] = {"command": str(PROXY), "args": []}
    else:
        servers.pop(NAME, None)
    CURSOR_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_CONFIG.write_text(json.dumps(config, indent=2) + "\n")


def install(host):
    if host == "cursor":
        edit_cursor(add=True)
    else:
        run(CLI_REMOVE[host], check=False)
        run(CLI_ADD[host])
    target = SKILL_DIRS[host] / NAME
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(SKILL, target)


def uninstall(host):
    if host == "cursor":
        edit_cursor(add=False)
    else:
        run(CLI_REMOVE[host], check=False)
    # Cursor and Grok share ~/.agents/skills: removing either removes it for both.
    shutil.rmtree(SKILL_DIRS[host] / NAME, ignore_errors=True)


def check():
    configs = sorted(ENGINE.glob("*/.mcp.json"))
    if not configs:
        raise SystemExit(f"Codex computer-use engine not found under {ENGINE}; install and open the ChatGPT app.")
    print(f"engine: {configs[-1].parent.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["install", "uninstall", "check"])
    parser.add_argument("--host", action="append", choices=[*SKILL_DIRS, "all"], default=[])
    args = parser.parse_args()
    check()
    if args.action == "check":
        return
    hosts = list(SKILL_DIRS) if "all" in args.host or not args.host else args.host
    for host in hosts:
        if host != "cursor" and not shutil.which(CLI_ADD[host][0]):
            print(f"{host}: skipped, `{CLI_ADD[host][0]}` not on PATH")
            continue
        (install if args.action == "install" else uninstall)(host)
        print(f"{host}: {args.action}ed")
    if args.action == "install" and "cursor" in hosts:
        print("cursor: the Cursor CLI also needs the server in the project's .cursor/mcp.json and --approve-mcps.")


if __name__ == "__main__":
    sys.exit(main())
