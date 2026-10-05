# codex-cu

Use Codex's computer-use engine — real clicks, typing and screenshots in your
Chromium browsers and macOS apps — from **any MCP harness**: Claude Code, Cursor, Grok, Devin and
anything else that speaks MCP over stdio.

No Codex agent runs and no Codex tokens are spent. Your harness's own model
drives the tools; Codex only provides the engine.

> Unofficial and not affiliated with OpenAI. It relies on internals of the
> ChatGPT desktop app, so an app update can break it without notice.

## How it works

The ChatGPT desktop app ships its computer-use engine as a local MCP server,
`cua_repl` (one `js` tool running JavaScript against a `cua` automation API).
Used directly from another harness it has two gaps:

1. Its browser service rejects every call without Codex turn metadata
   (`Missing required Codex turn metadata: session_id, turn_id`).
2. Its JavaScript runs in a sandbox that cannot write files, so screenshots
   cannot be saved.

`codex_cu_mcp.py` is a small stdio proxy (Python standard library only) that:

- launches the newest `cua_repl` installed by the ChatGPT app, with the app's
  own command, arguments and environment, so app updates are picked up;
- adds the turn metadata to each tool call;
- ends the turn when your harness's turn ends (through its stop hook) and when
  the session exits, so the engine closes the tabs the agent opened, as Codex
  does after every turn;
- saves every image the engine returns to `~/.codex-cu/images/<pid>/` and
  appends `codex-cu saved image: <path> (<mime>)` to the result;
- deletes those images when the session ends, and sweeps the ones left behind
  by a proxy that was killed;
- forwards everything else unchanged, including the engine's confirmation
  requests for sensitive actions.

The repository also ships a short skill, `skills/codex-cu/SKILL.md`, that
teaches agents how to drive the engine safely.

## Requirements

- macOS with the [ChatGPT desktop app](https://chatgpt.com/download) installed,
  signed in and running, with its Codex computer-use plugin present
  (`~/.codex/plugins/cache/openai-bundled/unified-computer-use/`).
- For browsers: OpenAI's ChatGPT/Codex extension installed and connected in
  each Chromium browser you want to drive (Chrome, Helium, Edge, …). Every
  installation becomes a separate browser for the agent.
- For native apps: the Accessibility permission the ChatGPT app asks for.
- Python 3.9+.

## Install

```sh
git clone https://github.com/FranciscoJSBarragan/codex-cu-mcp.git ~/codex-cu-mcp
cd ~/codex-cu-mcp
python3 install.py check                  # confirms the engine is present
python3 install.py install --host all     # or: --host claude --host grok ...
```

`install` registers the `codex-cu` MCP server by the absolute path of this
checkout, adds its stop hook where the harness runs one, and copies the skill
into each harness. Hosts whose CLI is not on
`PATH` are skipped. After `git pull`, the proxy is already up to date; rerun
`install` only to refresh the skill. `uninstall` reverses all three.

### Per harness

What the installer does, if you prefer to do it by hand:

| Harness | MCP server | Stop hook | Skill |
| --- | --- | --- | --- |
| Claude Code | `claude mcp add -s user codex-cu -- <repo>/codex_cu_mcp.py` | `Stop` in `~/.claude/settings.json` | `~/.claude/skills/codex-cu/` |
| Cursor | `codex-cu` entry in `~/.cursor/mcp.json` | — | `~/.agents/skills/codex-cu/` |
| Grok | `grok mcp add -s user codex-cu <repo>/codex_cu_mcp.py` | `~/.grok/hooks/codex-cu.json` | `~/.agents/skills/codex-cu/` |
| Devin | `devin mcp add -s user codex-cu -- <repo>/codex_cu_mcp.py` | `Stop` in `~/.config/devin/config.json` | `~/.config/devin/skills/codex-cu/` |

The stop hook is one command, `"<repo>/codex_cu_mcp.py" end-turn`, in the
harness's usual hook format.

Cursor's JSON entry:

```json
{ "mcpServers": { "codex-cu": { "command": "/path/to/codex_cu_mcp.py", "args": [] } } }
```

Harness notes:

- **Claude Code** — in headless runs, allow the tool and the shell used to
  convert screenshots: `claude -p --allowedTools mcp__codex-cu__js Bash`.
- **Cursor** — the desktop app reads `~/.cursor/mcp.json`. The Cursor CLI
  (`cursor-agent`) does not: put the same entry in the project's
  `.cursor/mcp.json` and run with `--approve-mcps`. The CLI runs no stop
  hooks in print mode, so Cursor gets no hook; tabs are released when the
  agent closes them or the session exits.
- **Grok** — works from the user config; no extra steps.
- **Devin** — `devin -p` refuses untrusted directories; run it once
  interactively in the working directory to trust it. Without `-s user`, the
  server is added to the current project only.
- Cursor and Grok share `~/.agents/skills`, so uninstalling either removes the
  skill for both.

## Turns and tabs

Codex ends a computer-use turn after every agent turn: the engine then closes
the tabs that turn opened and detaches from the browser. `codex-cu` does the
same through the harness's stop hook. `codex_cu_mcp.py end-turn` signals
(`SIGUSR1`) only the proxy started by the hook's own harness process, so other
sessions keep their tabs. To keep a tab past the turn, the agent calls
`tab.markHandoff()` (reuse it in a later turn) or `tab.markDeliverable()`
(leave it open for you).

## Several browsers

Each browser with the extension has its own `metadata.extensionInstanceId`, but
they often all report the name "Chrome". The skill reads optional names from
`~/.codex-cu/browsers.json`:

```json
{ "Helium": "3f1c…", "Chrome (work)": "a043…" }
```

Without it, the agent opens a test tab in each browser, asks you which window
showed it, and writes the file. Any harness reads the same file.

## Usage

Ask your agent to use the `codex-cu` skill, for example:

> Use the codex-cu skill: open https://example.com in Chrome, save a PNG
> screenshot to ./shot.png and tell me the page title.

Direct tool use, one `js` call at a time:

```js
const tab = await cua.createBrowserTab("chrome", "https://example.com", { sessionName: "🧪 my task" });
await tab.getAXState();     // accessibility tree with element indexes
await tab.click(3);         // act on an index from the latest state
await tab.getScreenshot();  // -> "codex-cu saved image: ~/.codex-cu/images/<pid>/001.jpg (image/jpeg)"
await tab.close();
```

```js
const app = await cua.getApp("Calculator");  // native macOS app
```

Screenshots arrive as JPEG; convert with
`sips -s format png <saved path> --out shot.png`. Copy them before the session
ends — the proxy deletes its images on exit.

## Tested

macOS, ChatGPT app 26.930.31428–26.930.31730. Same skill-only prompt in every
harness: open example.com in Chrome, save a PNG screenshot, report the title,
close the tab.

| Harness | Model | Result | Time |
| --- | --- | --- | --- |
| Claude Code (`claude -p`) | Claude Sonnet 5.5 | ✅ | 11 s |
| Devin CLI | Claude Sonnet 5.5 (low) | ✅ | 16 s |
| Grok CLI | Grok 4.7 Build Fast (high) | ✅ | 26 s |
| Cursor CLI | Grok 4.7 High Fast | ✅ | 53 s |

Stop hooks were checked by running the four harnesses at once: Claude Code,
Devin and Grok each ended the turn of their own proxy only.

## Limits

- **Chrome and native apps only.** Codex's in-app browser exists only inside a
  Codex session and is not reachable here.
- **macOS only**, and only while the ChatGPT app is installed and running.
- **No hover.** The engine sends clicks, drags, scrolls and keystrokes.
- **Confirmations need MCP elicitation.** Sensitive actions (purchases,
  messages, uploads, settings) ask the client to confirm; a harness without
  elicitation gets them refused, never silently executed.
- **Killed sessions leave tabs behind.** If a harness kills the proxy outright
  (`SIGKILL`) before the turn ends, the engine keeps that session's open tabs
  and no other session can release them; close them by hand.
- **Unofficial.** If a ChatGPT update changes what the engine expects, calls
  fail with the engine's error; nothing falls back silently.

## Troubleshooting

- `Browser is not available: …` — that browser's extension is not connected.
  Open the browser and check the extension.
- `No tab with id …` — the turn that opened the tab has ended; open a new tab.
- `Tab … is already part of browser session …` — the tab belongs to another
  live or killed session; leave it alone.
- `Browser is not available: iab` — expected; see Limits.
- `codex-cu: no unified-computer-use .mcp.json` — the ChatGPT app or its
  computer-use plugin is missing; open the app once and enable computer use.
- `EPERM` when writing a file from `js` — expected: the engine's sandbox is
  read-only. Take the screenshot and use the saved path instead.

## License

MIT
