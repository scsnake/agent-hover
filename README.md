# agent-hover

[繁體中文說明](README.zh-TW.md)

Keep only a few AI coding agent tabs visible in **iTerm2**. The other agent tabs hide and keep running, and they come back when they need you. A small floating panel shows all agents at a glance. For Claude Code, Codex and the Antigravity CLI (`agy`) on macOS.

![Top: the panel with all agent tabs, a red flame on a busy agent that is not working, old tabs folded into one line, and that agent's CPU and memory on the left. Bottom: search with a Closed section](docs/panel.png)

- **Keep the tab bar short.** The tool keeps at most N agent tabs visible. It hides the others as iTerm2 **buried sessions**. They keep running.
- **Get the tab back when it matters.** When an agent waits for you or finishes, its tab comes back to its old place, but never while you type.
- **See every agent at a glance.** A small pill in the top-right corner shows the most recently active agent. Hover it to see all agent tabs: working, waiting for you, done, idle. A click brings that tab and iTerm2 to the front.

> **Status: proof of concept (POC).** It works for the author every day, on one Mac. Expect rough edges. Tested on macOS 15.8, iTerm2 3.7.3, Claude Code 2.1, Codex CLI. macOS and iTerm2 only.

## Features

| Feature | How |
|---|---|
| Agent status | Claude Code, Codex and Antigravity CLI (`agy`) hooks write each tab's state: **working**, **waiting** (permission, question, plan), **done**, **idle** |
| N active tabs | A daemon buries idle and long-working tabs, and unburies a tab when its agent waits or is done. It never changes tab titles |
| Floating panel | Hover the pill to see all agent tabs, most recent first. Click a row to raise that tab |
| Question preview | A waiting agent has an orange `?`. Hover its row to see the question: the command to approve, the choices, or the plan |
| Peek | Hover any other row to see the last lines of that tab's screen, without switching to it |
| CPU and memory | Hover the agent mark at the right end of a row: the frame shows the agent, model, CPU and memory (instead of the screen) of the agent and the commands it runs. A red flame marks an agent that is not working but uses much CPU (stuck, or a command still running) |
| "New" dot | A blue dot marks a done tab that you have not looked at yet |
| Pin | Right-click a row → **Pin**. A pinned tab is never hidden and is listed first |
| Search | Click the magnifier. Filter by tab name and agent folder, and optionally by chat text. Agents closed in the last 7 days show under **Closed**; a click opens a new tab in that folder with the resume command typed (press Enter to run it) |
| Edge bar | Drag the pill against the right edge of the screen, push a little past it and hold for a moment (a blue border appears), then release. It becomes a thin bar in the color of the most urgent state. Hover it to see the panel; drag it away from the edge to undock |
| Model | Hover a row to see the agent and its model (also a custom provider, for example Codex with a local model) |
| Safe typing | An automatic unbury waits until you stop typing for 3 s, so your keys never go to the wrong agent |
| Remote hosts | Agents in tmux panes on other machines (SSH) show in the same panel, with a server mark. Read-only: nothing is installed on the host. See [Remote hosts](#remote-hosts-tmux-over-ssh) |
| Settings | `~/.agent-hover/config.json`: font size, panel width, N, idle time |

## Requirements

- macOS (tested on 15.8) and **iTerm2 3.7 or later**, with its Python API on (**Settings → General → Magic → Enable Python API**).
- [uv](https://docs.astral.sh/uv/) (Python package manager).
- Xcode Command Line Tools (`xcode-select --install`). The panel is one Swift file, and `./run.sh panel` compiles it on your Mac the first time. So there is no unsigned app to download.
- Claude Code and/or Codex CLI.

`./run.sh doctor` checks all of this and prints a fix for each problem.

## Install

1. Get the code:
   ```sh
   git clone https://github.com/scsnake/agent-hover.git
   cd agent-hover
   ```
2. See what the install would change. This writes nothing:
   ```sh
   ./install.sh
   ```
3. Add the hooks. This first backs up `~/.claude/settings.json`, `~/.codex/config.toml` and (if agy is installed) `~/.gemini/config/hooks.json`:
   ```sh
   ./install.sh --apply
   ```
4. Codex only: start `codex`, type `/hooks`, and trust the new hooks. Codex skips hooks that are not trusted.
5. Start the daemon in **dry run** first. It only logs what it would do:
   ```sh
   ./run.sh start
   ./run.sh log
   ```
6. When the decisions look right, start the daemon for real, together with the panel:
   ```sh
   ./run.sh stop
   ./run.sh            # same as ./run.sh up
   ```
7. Optional: start both at login, and whenever iTerm2 runs:
   ```sh
   ./run.sh autostart on
   ```
   macOS may ask once to let it control iTerm2. Allow it. `./run.sh autostart off` removes it.

Start the daemon from a plain shell tab, not from inside an agent session.

## Use

| Action | Result |
|---|---|
| Hover the pill | The list opens |
| Move the mouse away | The list closes after 0.4 s |
| Click a row, or the pill | That tab comes back (if buried), is selected, and iTerm2 comes to the front |
| Hover a waiting row | A frame beside the list shows the agent's question |
| Hover any other row | After 0.35 s, a frame shows the last lines of that tab's screen (Peek) |
| Right-click a row | **Pin** / **Unpin** |
| Click the magnifier | Search mode: type to filter. Turn on **chat** to search the chat text. **Enter** raises the first tab; **Esc** ends the search |
| Drag the pill | Move the panel. Drop it at the right screen edge to get the edge bar |

The panel shows only on the first desktop Space. It is hidden on other Spaces and on full-screen apps.

The same search works in a terminal: `./run.sh search [--chat] [--closed] WORD`.

## Rules: when a tab is hidden or comes back

The daemon checks once per second.

- **Comes back:** the agent **waits** for you, or is **done** and you did not look at it yet. Also when its agent ends.
- **Hidden:** a **done** (seen) or **idle** tab after 20 min. A **working** tab is hidden only by the N limit below (set `daemon.working_park_s` to also hide it that many seconds after you leave it).
- **Too many tabs:** when more than N (5) agent tabs are visible, idle tabs go first, then done, then working.
- **Never hidden:** the tab you use, the selected tab of each window, waiting tabs, pinned tabs, the last tab of a window, tabs with split panes.
- **No surprises while you type:** an automatic unbury waits until no key was pressed for 3 s. Afterwards, the window you typed in gets the keyboard back.

Agent tabs that started before the install are found by their job name (`claude`, `codex`) within 5 s.

Other tabs (shells, SSH, other programs) are never touched.

## Settings

Copy `config.example.json` to `~/.agent-hover/config.json` and change what you need:

```json
{
  "panel":  {"font_size": 12, "list_width": 440, "hover_ms": 120,
             "hotkey": "ctrl+opt+space", "fold_idle_h": 12, "fold_waiting_h": 24, "closed_days": 7,
             "dock_px": 4, "dock_delay_ms": 500, "magnet_px": 24},
  "daemon": {"n_active": 5, "idle_min": 20, "working_park_s": 0,
             "cpu_hot_pct": 50, "cpu_hot_min": 2, "notify_limit_reset": true},
  "agents": {"extra": []},
  "remote": {"hosts": []},
  "update_check": false
}
```

- `panel` changes apply within 1 s. `daemon` changes apply after `./run.sh stop` and `./run.sh start --apply`.
- A command-line flag overrides the file, for example `./run.sh start --apply --n 8`.
- `panel.hover_ms`: the cursor must rest on the pill or bar this long (in ms) before the list opens, so a cursor that only passes over it does not open it. Default 120. `0` opens at once.
- `panel.hotkey`: opens the list in search mode from any app, also on another desktop Space. Press it again to close. Modifiers: `ctrl`, `opt`, `cmd`, `shift`; keys: letters, digits, `space`, `return`, `tab`, `f1`–`f12` and some punctuation. `"off"` turns it off. If it does not work, another app likely uses the same keys (for example Alfred or Raycast on `opt+space`).
- `panel.fold_idle_h`: tabs that are done or idle for more than this many hours fold into one line at the end of the list. Rest the cursor on the line, or click it, to show them. Folded tabs do not count in the pill. Pinned tabs, new (unseen) tabs and search results never fold. `0` turns it off.
- `panel.fold_waiting_h`: a question left unanswered for more than this many hours also folds (an abandoned session), and no longer colors the edge bar or counts in the pill. Default 24. `0`: waiting tabs never fold.
- `panel.closed_days`: the search also lists agents closed in the last N days (only real sessions, not `claude -p` or `codex exec` runs). Click one: a new tab runs `cd <folder>` and types `claude --resume <id>` (or `codex resume <id>`) without Enter. The command repeats the options the session started with (for example `--model`, `--effort`, `--permission-mode`, `--dangerously-skip-permissions`, Codex `-m`, `-s`, `-c`). It drops the prompt, `--continue`/`--resume`, the session ID, `--name` and `--worktree`. The daemon saves each command line in `~/.agent-hover/commands.json` (kept 60 days). Sessions that ended before the daemon saw them get the plain command. `0` turns it off.
- `panel.dock_px`, `panel.dock_delay_ms`: how sticky the screen edge is. A dragged panel docks only when you push it at least `dock_px` past the right edge and hold for `dock_delay_ms` (the blue border then shows). A small pull back undocks it.
- `panel.magnet_px`: a dragged panel within this distance of a screen edge snaps flush to it (also right after you pull it out of the bar). Hold ⌥ (Option) while you drag for no snap. `0` turns it off.
- `daemon.working_park_s`: hide a working tab this many seconds after you leave it. Default `0`: a working tab is hidden only when more than `n_active` agent tabs are visible (working tabs go last).
- `daemon.cpu_hot_pct`, `daemon.cpu_hot_min`: the red flame shows when an agent that is not working uses at least this % CPU for this many minutes. `cpu_hot_pct: 0` turns it off.
- `daemon.notify_limit_reset`: when a Codex tab waits on a usage limit, hover its agent mark to see when the limit resets. At the reset time, a macOS notification says the agent can continue (Codex does not go on by itself), and the agent mark blinks the first time you open the list after it (for 30 s, or until you click the panel). `false` turns the notification off. The first notification may ask you to allow notifications for Script Editor.
- `update_check`: `true` checks once a day for a newer version. The pill then shows **↑ update**, and `./run.sh doctor` says so. Run `./run.sh update` to get it. Off by default.
- Logs in `~/.agent-hover/log/` move to `<name>.1` at 5 MB; one old file is kept.
- `agents.extra`: more job names to find (for example `qwen`). Such an agent also needs hooks that call `agent_hover/hook.py <name>` with Claude-style events.
- Agent marks: `~/.agent-hover/agents.json`, for example `{"codex": {"mark": "◇", "color": "#10A37F"}}`.
- `remote.hosts`: see [Remote hosts](#remote-hosts-tmux-over-ssh). Empty by default: nothing connects.

## Remote hosts (tmux over SSH)

Agents that run in tmux on another machine (for example a GPU server) can show in the panel next to your local tabs. Nothing is installed on that machine: no files, no hooks, no background process, no settings change.

**Set up:**

1. Make sure that `ssh <host>` works with a key, without a password prompt.
2. Add the host to `~/.agent-hover/config.json`:

   ```json
   "remote": {"hosts": [
     {"name": "gpu-box", "connect": ["ssh", "gpu-box"]}
   ]}
   ```

3. Test it: `./run.sh remote check gpu-box`. It lists the agent panes it finds.
4. Restart the daemon: `./run.sh stop && ./run.sh start --apply`.

**Host settings:**

| Key | Meaning |
|---|---|
| `name` | Needed. The name in the panel |
| `connect` | Needed. The command that runs a shell command on the host: the command is added as its last argument. Any transport works: `["ssh", "-p", "2222", "me@10.0.0.5"]`, `["ssh", "-J", "jump", "gpu-box"]`. Without `connect`, the host is skipped |
| `attach` | The command typed into a new local tab when you click a row. `{pane}`, `{session}`, `{window}` and `{host}` are filled in. Default for an ssh `connect`: `ssh -t gpu-box 'tmux attach -t {pane} \; select-window -t {pane} \; select-pane -t {pane}'` |
| `interval_s` | Seconds between scans. Default 5 |
| `share_connection` | `false`: do not share one ssh connection (see below). Default `true` |
| `enabled` | `false`: skip this host |

**What runs on the host:** every `interval_s` seconds, the daemon runs one read-only shell script through `connect`. `./run.sh remote script` prints it. It only reads:

- `tmux list-panes`: the panes, their windows and titles.
- `ps`: which panes run `claude` or `codex`, and their CPU and memory. Only the first two words of each command line are sent.
- `~/.claude/sessions/*.json`: Claude Code's own status file of each running session (busy, idle or waiting; folder; tmux pane).
- The last 64 KB of each running Codex session file (`~/.codex/sessions`), and once a minute the model line of each agent's transcript.

Hover Peek runs `tmux capture-pane -p`. Only a click changes something. If a local tab is already attached to that tmux session, it selects the row's tmux window and pane there (`tmux select-window`, `select-pane`). Else it opens a local tab that runs `attach`. A tab you attached yourself is found by its ssh connection: the daemon runs `tmux list-clients` and reads each client's `SSH_CONNECTION` (Linux hosts), then compares it with the local ssh ports (`lsof`). A tab whose ssh goes through a ControlMaster uses the master's connection. When several tabs share one connection, the tab whose ssh started at the same time as the remote ssh session is used. The daemon log (`./run.sh log`) records the script at start, and each peek and select command.

**One shared connection:** to an ssh `connect`, the daemon adds `BatchMode=yes` (no password prompt), `ConnectTimeout=5`, `Compression=yes` and a shared connection (`ControlMaster=auto`, `ControlPersist=120`, control socket in `~/.agent-hover/`). It adds each option only when your command does not set it. The first scan logs in once; each later scan reuses that connection (about 0.1 s instead of 1–2 s). This is a feature of the OpenSSH client and needs no change on the server. Windows OpenSSH does not support it: use `"share_connection": false` there.

**Limits compared with local tabs (hooks):**

- A state change shows after up to `interval_s` seconds, not at once.
- Claude: busy, idle and waiting come from Claude's status file. Claude does not write "done": an agent that goes from working to idle while the daemon watches shows as **done** until you look at it. A turn shorter than one scan can be missed. A usage-limit or sign-in stop shows as idle, and the row has no question text (Peek shows the screen).
- Codex: working, done, error, interrupted and the usage limit (with its reset time) come from the end of its session file. A Codex approval wait may show as working (not verified).
- Only agents in tmux panes show. A Codex session file is found through `/proc` (Linux) or `lsof`.
- Remote rows are not hidden or moved: there is no local tab to bury.

## Commands

| Command | Does |
|---|---|
| `./run.sh` or `./run.sh up` | Start the daemon (`--apply`) and the panel |
| `./run.sh down` | Stop the panel and the daemon (it unburies its tabs first). Also pauses autostart until the next `up` |
| `./run.sh doctor` | Check the setup and print a fix for each problem |
| `./run.sh update` | Get the new version (`git pull --ff-only`, `uv sync`) and restart the daemon and the panel if they run |
| `./run.sh autostart on` / `off` / `status` | Start at login and whenever iTerm2 runs (a LaunchAgent); `off` removes it |
| `./run.sh start [--apply]` | Start the daemon only. Dry run without `--apply` |
| `./run.sh stop` | Stop the daemon only. It unburies every tab it buried first |
| `./run.sh status` | Mode, buried count, last log lines |
| `./run.sh restore` | Unbury every tab it buried (also after a crash) |
| `./run.sh log` | Follow the decision log |
| `./run.sh panel` / `panel-stop` | Start / stop the panel |
| `./run.sh search [--chat] [--closed] WORD` | Find agent tabs by name, folder, chat text; `--closed` adds recently closed agents with their resume command |
| `./run.sh remote script` / `remote check NAME` | Print the read-only script for remote hosts / run one scan on a host and list its agent panes |

## What it changes, and how to undo it

- **Hooks:** tagged entries in `~/.claude/settings.json`, a marked block in `~/.codex/config.toml`, and an `agent-hover` entry in `~/.gemini/config/hooks.json` (made if agy is installed and the file is missing; restore removes it again). Nothing else in these files changes. The install backs up the files first, to `~/.agent-hover/backup/<time>/`.
- **agy:** Antigravity reads `~/.gemini/config/hooks.json` in the agy CLI, the Antigravity IDE and the app. In the IDE and the app the hook finds no iTerm2 tab and does nothing. It always answers `allow` to PreToolUse, so agy's own permission rules still decide.
- **Data:** `~/.agent-hover/` holds state files, logs, pins and backups. Only your user can read it.
- **Autostart (only if you turn it on):** `~/Library/LaunchAgents/local.agent-hover.plist`.
- **iTerm2:** it buries and unburies agent tabs, and moves an unburied tab back to its old place. iTerm2's own Claude Code integration is not changed.

To undo:

```sh
./uninstall.sh                        # dry run: shows what it removes
./uninstall.sh --apply                # remove autostart, stop, unbury all, remove only these hooks
./uninstall.sh --restore-backup       # dry run: put back the exact files from before the install
./uninstall.sh --restore-backup --apply
```

**Warning:** read this before you use `--force` with `--restore-backup`. If a file changed after the install, the restore refuses, to protect later changes. `--force` restores anyway, and those changes are lost.

## Privacy

Everything stays on your Mac. There are no network calls, except:

- the update check if you turn it on (`"update_check": true`): once a day, `git ls-remote` asks GitHub for the newest commit ID. It only shows a mark; it never downloads or installs.
- the remote hosts that you add (`"remote"`): the read-only scan through your own `connect` command (see [Remote hosts](#remote-hosts-tmux-over-ssh)). Its result stays in memory and in `~/.agent-hover/panel.json`.

- The hooks store each agent's state, its folder, and the text of a pending question.
- The chat search and the model name in the hover frame read the agents' own transcript files (`~/.claude/projects`, `~/.codex/sessions`), only when you search or for the open tabs.
- The chat search keeps a copy of the message text (no tool output) in `~/.agent-hover/chatcache/`, so a search takes well under 0.1 s. Entries of deleted transcripts are removed at the next chat search. You can delete the folder at any time; the next search builds it again.
- For an agent tab with no hook event yet, the daemon reads Claude Code's own process file (`~/.claude/sessions/<pid>.json`) for its session ID, folder and state. It also reads the process list (`ps`) for CPU and memory.
- Claude Code reports its own usage limits through hooks: a turn stopped by a usage limit or an account problem (`StopFailure`), or a limit that reset while the Mac slept (`quota_auto_resume_stale`), shows as **waiting** with Claude's message.
- For a working Codex tab, the daemon reads the end of its session file (`~/.codex/sessions`) every 5 s. Codex sends no hook when a turn ends with an error, so this is how a usage-limit stop shows as **waiting** (Codex then asks whether to switch to a smaller model). A Codex tab with no hook event yet is matched to its session file through the files its process has open (`lsof`).
- Newer Codex versions run their hooks in the `codex app-server` daemon, not under the `codex` in the tab. The daemon keeps the environment of the tab that started it, so `$ITERM_SESSION_ID` names the wrong tab. The hook sees that its parent is `codex app-server`. It then finds the tab through the session ID (the open Codex tab whose record names it), or else through the folder: the open Codex tab with the same folder, a tab with no session yet first. Two new Codex tabs in the same folder: the event is skipped (no guess) and logged as `unrouted` in `events.jsonl`.
- Peek reads the hovered tab's screen through iTerm2, and keeps the last lines in `~/.agent-hover/peek.json`.

## How it works

```
Claude Code / Codex hooks ──► ~/.agent-hover/state/<tab>.json
                                        │
                     daemon (iTerm2 Python API, 1 s tick)
                       │ rules: bury / unbury        │ writes panel.json
                       ▼                             ▼
                 iTerm2 tabs                 floating panel (Swift)
                                             clicks ──► requests/ ──► daemon
```

| File | Role |
|---|---|
| `agent_hover/hook.py` | The hook. Standard library only, always exits 0, prints nothing |
| `agent_hover/states.py` | Hook event → state; state files; question text |
| `agent_hover/rules.py` | When to bury and unbury (pure logic) |
| `agent_hover/daemon.py` | The iTerm2 daemon |
| `agent_hover/search.py` | Search by name, folder, chat text; model from the transcript |
| `agent_hover/config_edit.py` | Install, uninstall and restore of the hook entries |
| `panel/AgentHoverPanel.swift` | The floating panel |

Tests: `uv run pytest`. The end-to-end test `tests/e2e_sandbox.py` opens a test window with fake agent tabs. **Stop your daemon first** (`./run.sh stop`).

## Limits

- macOS and iTerm2 only. Only iTerm2 can hide a tab and keep it running.
- Claude Code, Codex and agy only, out of the box.
- agy has no hook event for a permission prompt. Every 2 s the daemon reads each working agy tab's conversation database (`~/.gemini/antigravity-cli/conversations/<id>.db`, read-only): a step marked waiting shows the row as **waiting**, with the command as the question. This uses agy's internal format, which may change. `agy -p` runs (for example a review that another agent starts in its tab) are ignored.
- Remote agents show only when they run in tmux, and only from the hosts in `remote.hosts`. Their state is polled, so it is less exact than with hooks (see [Remote hosts](#remote-hosts-tmux-over-ssh)).
- Split-pane tabs are listed, but not hidden or moved.
- The panel and the daemon use iTerm2 and macOS behavior that may change in later versions. For example, the Space check uses a private macOS call; if it fails, the panel simply shows on every Space.

## Related projects

Many tools show agent status. As far as we found (October 2026), none hides and restores iTerm2 tabs. Pick the one that fits how you work:

| Tool | UI | Agents | Hides tabs | Notes |
|---|---|---|---|---|
| **This tool** | Floating panel + iTerm2 tab management | Claude Code, Codex | **Yes** (N visible, buried sessions) | Never changes tab titles; waits while you type |
| [iTerm2 3.7 built-in](https://iterm2.com/claude-code-integration.html) | Session Status in iTerm2 | Claude Code | No | Native; status dot, detail line, click to jump. This tool works next to it |
| [Megadesk](https://github.com/saugon/megadesk) | Floating panel + menu bar | Claude Code, Codex | No | Focus per tab or pane in iTerm2 and Ghostty; hotkeys; PR/CI tracking |
| [cc-status-bar](https://github.com/usedhonda/cc-status-bar) | Menu bar | Claude Code, Codex | No | Can focus the waiting terminal for you; hotkey to the most urgent session |
| [CodeIsland](https://github.com/wxtsky/CodeIsland), [Notchi](https://github.com/sk-ruban/notchi) | MacBook notch | Many | No | Approve or answer from the notch |
| [cmux](https://github.com/manaflow-ai/cmux) | Its own terminal (Ghostty-based) | Any | — | Vertical tabs, notification rings. For people who want a new terminal |

Ideas for the agent events came from [agent-board](https://github.com/WenyuChiou/agent-board), a shared progress board for many agents.

## License

MIT. See [LICENSE](LICENSE).
