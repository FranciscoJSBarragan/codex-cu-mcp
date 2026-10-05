#!/usr/bin/env python3
"""Register codex-cu, its skill and its stop hook with each harness, or remove them.

Each harness keeps its own MCP configuration; this uses the harness CLI where
one exists and edits JSON directly otherwise. The proxy is registered by its
absolute path in this checkout, so `git pull` updates every harness at once.
The stop hook ends the session's computer-use turn, which closes the tabs the
agent opened during that turn.
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
CURSOR_MCP = HOME / ".cursor/mcp.json"
END_TURN = f'"{PROXY}" end-turn'

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
# (config file, keys of the stop-hook list, hook entry) per harness.
STOP_HOOKS = {
    "claude": (HOME / ".claude/settings.json", ("hooks", "Stop"),
               {"hooks": [{"type": "command", "command": END_TURN}]}),
    "devin": (HOME / ".config/devin/config.json", ("hooks", "Stop"),
              {"hooks": [{"type": "command", "command": END_TURN}]}),
    "grok": (HOME / ".grok/hooks/codex-cu.json", ("hooks", "Stop"),
             {"hooks": [{"type": "command", "command": END_TURN}]}),
}
# The Cursor CLI runs no stop hooks in print mode, so Cursor gets none.


def run(argv, check=True):
    result = subprocess.run(argv, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SystemExit(f"{' '.join(argv[:3])} failed: {(result.stderr or result.stdout).strip()}")
    return result


def load_json(path):
    return json.loads(path.read_text()) if path.exists() else {}


def save_json(path, config):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n")


def is_ours(hook):
    text = json.dumps(hook)
    return str(PROXY) in text and "end-turn" in text


def set_server(host, add):
    if host == "cursor":
        config = load_json(CURSOR_MCP)
        servers = config.setdefault("mcpServers", {})
        servers.pop(NAME, None)
        if add:
            servers[NAME] = {"command": str(PROXY), "args": []}
        save_json(CURSOR_MCP, config)
        return
    run(CLI_REMOVE[host], check=False)
    if add:
        run(CLI_ADD[host])


def set_stop_hook(host, add):
    if host not in STOP_HOOKS:
        return
    path, keys, entry = STOP_HOOKS[host]
    if host == "grok":
        # Grok loads every file in ~/.grok/hooks; this one is entirely ours.
        if add:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"hooks": {"Stop": [entry]}}, indent=2) + "\n")
        else:
            path.unlink(missing_ok=True)
        return
    config = load_json(path)
    hooks = config.setdefault(keys[0], {})
    hooks[keys[1]] = [hook for hook in hooks.get(keys[1], []) if not is_ours(hook)]
    if add:
        hooks[keys[1]].append(entry)
    save_json(path, config)


def set_skill(host, add):
    target = SKILL_DIRS[host] / NAME
    # Cursor and Grok share ~/.agents/skills: removing either removes it for both.
    shutil.rmtree(target, ignore_errors=True)
    if add:
        shutil.copytree(SKILL, target)


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
    add = args.action == "install"
    hosts = list(SKILL_DIRS) if "all" in args.host or not args.host else args.host
    for host in hosts:
        if host != "cursor" and not shutil.which(CLI_ADD[host][0]):
            print(f"{host}: skipped, `{CLI_ADD[host][0]}` not on PATH")
            continue
        set_server(host, add)
        set_stop_hook(host, add)
        set_skill(host, add)
        print(f"{host}: {args.action}ed")
    if add and "cursor" in hosts:
        print("cursor: the Cursor CLI also needs the server in the project's .cursor/mcp.json and --approve-mcps.")


if __name__ == "__main__":
    sys.exit(main())
