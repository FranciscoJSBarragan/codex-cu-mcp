#!/usr/bin/env python3
"""Expose Codex's bundled computer-use engine (cua_repl) to any MCP client.

cua_repl's browser service requires Codex turn metadata on every tool call.
This stdio proxy launches the newest installed cua_repl with its own command,
args and env, forwards JSON-RPC in both directions, and adds that metadata to
tools/call requests that lack it. No Codex agent or model is used; the calling
harness's model drives the tools.

Ending a turn makes the engine close the tabs that turn opened and detach from
the browser, as Codex does after every turn. SIGUSR1 ends the current turn and
starts a new one; `codex_cu_mcp.py end-turn`, run from a harness's stop hook,
sends it to the proxy of that hook's own session. Exit also ends the turn.

The cua_repl kernel is sandboxed and cannot write files, so images it emits
(screenshots) are saved here, outside that sandbox, and their paths are
appended to the tool result for the harness to copy or convert. They live only
as long as the proxy process: removed on exit, and swept at startup when a
previous proxy was killed before it could clean up.
"""

import base64
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import uuid
from pathlib import Path

WINDOWS = sys.platform == "win32"
PLUGIN_DIR = Path.home() / ".codex/plugins/cache/openai-bundled/unified-computer-use"
CODEX_CONFIG = Path.home() / ".codex/config.toml"
IMAGE_ROOT = Path(os.environ.get("CODEX_CU_IMAGE_DIR", Path.home() / ".codex-cu/images"))
META_KEY = "x-codex-turn-metadata"
SANDBOX_META_KEY = "codex/sandbox-state-meta"
CONTROL_ID_PREFIX = "codex-cu-"
EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def version_key(path):
    return tuple(int(part) if part.isdigit() else 0 for part in path.name.split("."))


def load_server_config():
    if WINDOWS:
        import tomllib

        servers = tomllib.loads(CODEX_CONFIG.read_text(encoding="utf-8")).get("mcp_servers", {})
        if "node_repl" not in servers:
            sys.exit(f"codex-cu: no [mcp_servers.node_repl] in {CODEX_CONFIG}")
        return servers["node_repl"]
    configs = [d / ".mcp.json" for d in PLUGIN_DIR.iterdir() if (d / ".mcp.json").is_file()]
    if not configs:
        sys.exit(f"codex-cu: no unified-computer-use .mcp.json under {PLUGIN_DIR}")
    newest = max(configs, key=lambda p: version_key(p.parent))
    return json.loads(newest.read_text())["mcpServers"]["cua_repl"]


def is_running(pid):
    if WINDOWS:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def sweep_orphaned_images():
    # Image directories are named by the owning proxy's PID.
    if not IMAGE_ROOT.is_dir():
        return
    for directory in IMAGE_ROOT.iterdir():
        if directory.name.isdigit() and not is_running(int(directory.name)):
            shutil.rmtree(directory, ignore_errors=True)


def end_turn_for_caller():
    """Signal the proxies started by the nearest ancestor of this process.

    A harness spawns both its MCP servers and its hook commands, so the closest
    ancestor that owns a proxy is the hook's own session; proxies of other
    sessions hang off other processes and are left alone.
    """
    table = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,command="], capture_output=True, text=True).stdout
    parents, proxies = {}, {}
    script = Path(__file__).name
    for line in table.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, ppid, command = int(parts[0]), int(parts[1]), parts[2]
        parents[pid] = ppid
        if script in command and "end-turn" not in command:
            proxies.setdefault(ppid, []).append(pid)
    pid = os.getpid()
    while pid in parents and pid > 1:
        pid = parents[pid]
        if pid in proxies:
            for proxy in proxies[pid]:
                os.kill(proxy, signal.SIGUSR1)
            log = os.environ.get("CODEX_CU_HOOK_LOG")
            if log:
                with open(log, "a") as handle:
                    handle.write(f"ended turn: proxies {proxies[pid]} of {pid}\n")
            return


def main():
    sweep_orphaned_images()
    config = load_server_config()
    env = {**os.environ, **config.get("env", {})}
    child = subprocess.Popen(
        [config["command"], *config.get("args", [])],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env=env,
    )
    sandbox_state = {
        "permissionProfile": {"type": "disabled"},
        "sandboxCwd": Path.cwd().as_uri(),
        "useLegacyLandlock": False,
        "useMxc": False,
    }
    session_id = f"codex-cu-{uuid.uuid4()}"
    turn = {"session_id": session_id, "turn_id": str(uuid.uuid4())}
    image_dir = IMAGE_ROOT / str(os.getpid())
    image_count = 0
    in_lock = threading.RLock()
    out_lock = threading.Lock()
    turn_closed = {}

    def to_child(message):
        with in_lock:
            child.stdin.write((json.dumps(message) + "\n").encode())
            child.stdin.flush()

    def to_client(data):
        with out_lock:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

    def end_turn():
        """Close the current turn in the engine and start a new one."""
        with in_lock:
            request_id = f"{CONTROL_ID_PREFIX}{uuid.uuid4()}"
            done = turn_closed[request_id] = threading.Event()
            to_child({
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": "turn_ended", "arguments": {"hook_event_name": "Stop", **turn}},
            })
            turn["turn_id"] = str(uuid.uuid4())
        return done

    def save_images(message):
        nonlocal image_count
        content = (message.get("result") or {}).get("content")
        if not isinstance(content, list):
            return False
        saved = []
        for block in content:
            if block.get("type") != "image" or not block.get("data"):
                continue
            image_count += 1
            image_dir.mkdir(parents=True, exist_ok=True)
            ext = EXTENSIONS.get(block.get("mimeType"), "bin")
            path = image_dir / f"{image_count:03d}.{ext}"
            path.write_bytes(base64.b64decode(block["data"]))
            saved.append(f"{path} ({block.get('mimeType', 'unknown')})")
        if saved:
            content.append({"type": "text", "text": "codex-cu saved image: " + ", ".join(saved)})
        return bool(saved)

    def pump_child_output():
        for raw in child.stdout:
            try:
                message = json.loads(raw)
            except ValueError:
                to_client(raw)
                continue
            if not isinstance(message, dict):
                to_client(raw)
                continue
            # Responses to the proxy's own requests never reach the client.
            request_id = message.get("id")
            if isinstance(request_id, str) and request_id in turn_closed:
                turn_closed.pop(request_id).set()
                continue
            if save_images(message):
                raw = (json.dumps(message) + "\n").encode()
            to_client(raw)

    threading.Thread(target=pump_child_output, daemon=True).start()
    if not WINDOWS:
        signal.signal(signal.SIGUSR1, lambda *_: end_turn())
    # Harnesses usually stop MCP servers with a signal rather than closing stdin.
    for name in ("SIGTERM", "SIGHUP", "SIGINT"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: sys.exit(0))

    try:
        for raw in sys.stdin.buffer:
            if not raw.strip():
                continue
            try:
                message = json.loads(raw)
            except ValueError:
                with in_lock:
                    child.stdin.write(raw)
                    child.stdin.flush()
                continue
            if isinstance(message, dict) and message.get("method") == "tools/call":
                with in_lock:
                    meta = message.setdefault("params", {}).setdefault("_meta", {})
                    meta.setdefault(META_KEY, json.dumps(turn))
                    if WINDOWS:
                        meta.setdefault(SANDBOX_META_KEY, sandbox_state)
                    to_child(message)
                continue
            to_child(message)
    finally:
        # Close this session's tabs before the engine goes away.
        if not WINDOWS:
            signal.signal(signal.SIGUSR1, signal.SIG_IGN)
        try:
            end_turn().wait(timeout=5)
            child.stdin.close()
            child.wait(timeout=5)
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
            pass
        if child.poll() is None:
            child.kill()
        shutil.rmtree(image_dir, ignore_errors=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["end-turn"]:
        end_turn_for_caller()
    else:
        main()
