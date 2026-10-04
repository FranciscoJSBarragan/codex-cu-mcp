#!/usr/bin/env python3
"""Expose Codex's bundled computer-use engine (cua_repl) to any MCP client.

cua_repl's browser service requires Codex turn metadata on every tool call.
This stdio proxy launches the newest installed cua_repl with its own command,
args and env, forwards JSON-RPC in both directions, and adds that metadata to
tools/call requests that lack it. No Codex agent or model is used; the calling
harness's model drives the tools.

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

PLUGIN_DIR = Path.home() / ".codex/plugins/cache/openai-bundled/unified-computer-use"
IMAGE_ROOT = Path(os.environ.get("CODEX_CU_IMAGE_DIR", Path.home() / ".codex-cu/images"))
META_KEY = "x-codex-turn-metadata"
EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def version_key(path):
    return tuple(int(part) if part.isdigit() else 0 for part in path.name.split("."))


def load_server_config():
    configs = [d / ".mcp.json" for d in PLUGIN_DIR.iterdir() if (d / ".mcp.json").is_file()]
    if not configs:
        sys.exit(f"codex-cu: no unified-computer-use .mcp.json under {PLUGIN_DIR}")
    newest = max(configs, key=lambda p: version_key(p.parent))
    return json.loads(newest.read_text())["mcpServers"]["cua_repl"]


def is_running(pid):
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
    # One proxy process is one client session; the turn spans its lifetime.
    turn = {"session_id": f"codex-cu-{uuid.uuid4()}", "turn_id": str(uuid.uuid4())}
    image_dir = IMAGE_ROOT / str(os.getpid())
    image_count = 0
    out_lock = threading.Lock()

    def to_child(message):
        child.stdin.write((json.dumps(message) + "\n").encode())
        child.stdin.flush()

    def to_client(data):
        with out_lock:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

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
            if isinstance(message, dict) and save_images(message):
                raw = (json.dumps(message) + "\n").encode()
            to_client(raw)

    threading.Thread(target=pump_child_output, daemon=True).start()
    # Harnesses usually stop MCP servers with a signal rather than closing stdin.
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, lambda *_: sys.exit(0))

    try:
        for raw in sys.stdin.buffer:
            if not raw.strip():
                continue
            try:
                message = json.loads(raw)
            except ValueError:
                child.stdin.write(raw)
                child.stdin.flush()
                continue
            if isinstance(message, dict) and message.get("method") == "tools/call":
                meta = message.setdefault("params", {}).setdefault("_meta", {})
                meta.setdefault(META_KEY, turn)
            to_child(message)
        # Let trusted libraries release per-turn resources before shutdown.
        to_child({
            "jsonrpc": "2.0",
            "id": f"codex-cu-shutdown-{uuid.uuid4()}",
            "method": "tools/call",
            "params": {"name": "turn_ended", "arguments": {"hook_event_name": "Stop", **turn}},
        })
        child.stdin.close()
        child.wait(timeout=5)
    except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
        pass
    finally:
        if child.poll() is None:
            child.kill()
        shutil.rmtree(image_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
