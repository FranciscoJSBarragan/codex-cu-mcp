# codex-cu

Use Codex's computer-use engine (Chrome and macOS apps) from any MCP harness —
Claude Code, Cursor, Grok, Devin — without running a Codex agent or spending
Codex tokens. The calling harness's model drives the tools.

The ChatGPT desktop app ships the engine as a local MCP server, `cua_repl`.
`codex_cu_mcp.py` is a stdio proxy that launches the newest installed copy,
adds the Codex turn metadata its browser service requires, and saves emitted
screenshots outside the engine's write-protected sandbox.

This is unofficial: a ChatGPT update can change what the engine expects.

## Requirements

- macOS with the ChatGPT app installed and open.
- For Chrome: the Codex Chrome extension connected.
- Python 3.

## Install

```sh
git clone <this repo> ~/Code/codex-cu-mcp
python3 ~/Code/codex-cu-mcp/install.py install --host all   # or --host claude --host grok ...
```

This registers the `codex-cu` MCP server by its absolute path and copies the
`codex-cu` skill into each harness. `git pull` updates the proxy for every
harness; rerun `install` after skill changes. `uninstall` reverses it, and
`check` only verifies the engine is present.

The Cursor CLI ignores `~/.cursor/mcp.json`; add the same entry to the
project's `.cursor/mcp.json` and run it with `--approve-mcps`.

## Limits

- Chrome (through its extension) and native apps only. Codex's in-app browser
  is not reachable outside a Codex session.
- Screenshots land in `~/.codex-cu/images/<session>/` and are deleted when the
  harness's MCP session ends; copy them where they belong.
- Sensitive actions ask the client for confirmation through MCP elicitation; a
  harness without elicitation gets them refused, not executed.
