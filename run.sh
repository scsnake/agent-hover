#!/usr/bin/env bash
# agent-hover control.
#   ./run.sh [up] [--n 5 ...]  start the daemon (--apply) and the panel (no argument = up)
#   ./run.sh down        stop the panel and the daemon (the daemon unburies its tabs first)
#   ./run.sh doctor      check the setup and print a fix for each problem
#   ./run.sh autostart on|off|status   start at login (LaunchAgent); off removes it
#   ./run.sh start [--apply] [--n 5] [--idle-min 20] [--working-park-s 120]   daemon only
#   ./run.sh stop        daemon only; it unburies every tab it buried
#   ./run.sh status
#   ./run.sh restore     unbury every tab this tool buried (also after a crash)
#   ./run.sh log         follow the decision log
#   ./run.sh panel       build (if needed) and start the hover panel
#   ./run.sh panel-stop  stop the hover panel
#   ./run.sh search [--chat] WORD   find agent tabs by name, folder (and chat text)
set -euo pipefail
cd "$(dirname "$0")"
HOME_DIR="${AGENT_HOVER_HOME:-$HOME/.agent-hover}"
PID="$HOME_DIR/daemon.pid"
PANEL_PID="$HOME_DIR/panel.pid"
PAUSED="$HOME_DIR/paused"  # written by "down": autostart's supervise loop then starts nothing
LABEL="local.agent-hover"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$HOME_DIR/log"

# True only if the PID file names a live process that is our daemon (not a reused PID).
alive() {
  [[ -f "$PID" ]] || return 1
  local p; p="$(cat "$PID")"
  [[ "$p" =~ ^[0-9]+$ ]] || return 1
  ps -p "$p" -o command= 2>/dev/null | grep -q 'agent_hover\.daemon'
}

panel_alive() {
  [[ -f "$PANEL_PID" ]] && ps -p "$(cat "$PANEL_PID")" -o command= 2>/dev/null | grep -q agent-hover-panel
}

mode() { [[ " $1 " == *" --apply "* ]] && echo "APPLY mode" || echo "DRY-RUN mode"; }

case "${1:-up}" in
  up)
    [[ $# -gt 0 ]] && shift
    rm -f "$PAUSED"
    "$0" start --apply "$@" || echo "daemon: see the message above" >&2
    "$0" panel
    ;;
  down)
    touch "$PAUSED"
    "$0" panel-stop
    "$0" stop
    ;;
  start)
    shift
    if alive; then
      RUNNING="$(ps -p "$(cat "$PID")" -o command= | sed 's/.*agent_hover\.daemon *//')"
      if [[ "$RUNNING" == "$*" ]]; then
        echo "already running (pid $(cat "$PID")), $(mode "$RUNNING"), same options"; exit 0
      fi
      echo "already running (pid $(cat "$PID")) in $(mode "$RUNNING") with options: '${RUNNING}'" >&2
      echo "you asked for $(mode "$*") with options: '$*'" >&2
      echo "nothing changed. Run ./run.sh stop first, then start again." >&2
      exit 1
    fi
    uv sync -q
    # The daemon holds an exclusive lock, so a second start exits at once (see daemon.py).
    nohup .venv/bin/python -m agent_hover.daemon "$@" >> "$HOME_DIR/log/daemon.out" 2>&1 &
    NEW=$!
    sleep 1
    if kill -0 "$NEW" 2>/dev/null; then
      echo "$NEW" > "$PID"; echo "started (pid $NEW), $(mode "$*"), options: '$*'"
    else
      echo "not started; last lines of $HOME_DIR/log/daemon.out:"; tail -n 3 "$HOME_DIR/log/daemon.out"; exit 1
    fi
    ;;
  stop)
    if ! alive; then echo "not running"; rm -f "$PID"; exit 0; fi
    kill -TERM "$(cat "$PID")"
    # The daemon unburies each tab when the keyboard is quiet (up to 5 s wait per tab).
    for _ in $(seq 240); do alive || break; sleep 0.5; done
    if alive; then echo "still running after 120 s (pid $(cat "$PID")); buried tabs may not be restored" >&2; exit 1; fi
    rm -f "$PID"; echo "stopped (buried tabs restored)"
    if [[ -f "$PLIST" && ! -f "$PAUSED" ]]; then
      echo "note: autostart is on, so it starts the daemon again within 10 s. Use ./run.sh down to stop for good."
    fi
    ;;
  status)
    if alive; then
      RUNNING="$(ps -p "$(cat "$PID")" -o command= | sed 's/.*agent_hover\.daemon *//')"
      echo "running (pid $(cat "$PID")), $(mode "$RUNNING"), options: '${RUNNING}'"
    else
      echo "not running"
    fi
    echo "buried by agent-hover: $(python3 -c "import json,sys;print(len(json.load(open(sys.argv[1]))))" "$HOME_DIR/buried.json" 2>/dev/null || echo 0)"
    tail -n 5 "$HOME_DIR/log/daemon.jsonl" 2>/dev/null | cut -c1-200 || true
    ;;
  restore)
    uv sync -q
    .venv/bin/python -m agent_hover.daemon --restore
    ;;
  log)
    tail -f "$HOME_DIR/log/daemon.jsonl"
    ;;
  panel)
    if panel_alive; then echo "panel already running (pid $(cat "$PANEL_PID"))"; exit 0; fi
    if [[ ! -x .build/agent-hover-panel || panel/AgentHoverPanel.swift -nt .build/agent-hover-panel ]]; then
      mkdir -p .build
      swiftc -O -swift-version 5 panel/AgentHoverPanel.swift -o .build/agent-hover-panel
    fi
    nohup .build/agent-hover-panel >> "$HOME_DIR/log/panel.out" 2>&1 &
    echo $! > "$PANEL_PID"; echo "panel started (pid $!)"
    alive || echo "note: the daemon is not running; the panel shows 'daemon off'"
    ;;
  panel-stop)
    if panel_alive; then
      kill -TERM "$(cat "$PANEL_PID")"; rm -f "$PANEL_PID"; echo "panel stopped"
    else
      rm -f "$PANEL_PID"; echo "panel not running"
    fi
    ;;
  search)
    shift
    .venv/bin/python -m agent_hover.search "$@"
    ;;
  doctor)
    BAD=0
    ok() { echo "  ✓ $1"; }
    bad() { echo "  ✗ $1"; echo "      fix: $2"; BAD=1; }
    note() { echo "  · $1"; }
    echo "agent-hover doctor"
    ok "macOS $(sw_vers -productVersion)"
    ITV="$(defaults read /Applications/iTerm.app/Contents/Info CFBundleShortVersionString 2>/dev/null || true)"
    if [[ -z "$ITV" ]]; then bad "iTerm2 not found in /Applications" "install iTerm2 3.7 or later from iterm2.com"
    elif [[ "$(printf '%s\n3.7\n' "$ITV" | sort -V | head -1)" != "3.7" ]]; then bad "iTerm2 $ITV is older than 3.7" "update iTerm2"
    else ok "iTerm2 $ITV"; fi
    if [[ "$(defaults read com.googlecode.iterm2 EnableAPIServer 2>/dev/null || echo 0)" == 1 ]]; then ok "iTerm2 Python API is on"
    else bad "iTerm2 Python API is off" "iTerm2 → Settings → General → Magic → Enable Python API"; fi
    if command -v uv >/dev/null; then ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"
    else bad "uv not found" "install uv: https://docs.astral.sh/uv/"; fi
    if xcrun --find swiftc >/dev/null 2>&1 || command -v swiftc >/dev/null; then ok "swiftc (for the panel)"
    else bad "swiftc not found" "xcode-select --install"; fi
    # Hooks: Claude Code and/or Codex. One agent with hooks from this folder is enough.
    HOOK="$PWD/agent_hover/hook.py"
    HOOKED=0
    for pair in "Claude Code:$HOME/.claude/settings.json" "Codex:$HOME/.codex/config.toml"; do
      NAME="${pair%%:*}"; FILE="${pair#*:}"
      if [[ ! -f "$FILE" ]]; then note "$NAME: no $FILE (fine if you do not use $NAME)"
      elif grep -q "$HOOK" "$FILE"; then
        ok "$NAME hooks point to this folder"; HOOKED=1
        [[ "$NAME" == Codex ]] && note "Codex: trust the hooks once inside codex with /hooks"
      elif grep -q "agent_hover/hook.py" "$FILE"; then bad "$NAME hooks point to another folder" "./uninstall.sh --apply in the old folder, then ./install.sh --apply here"
      else note "$NAME hooks are not installed (./install.sh --apply)"; fi
    done
    [[ "$HOOKED" == 1 ]] || bad "no agent has hooks from this folder" "./install.sh, then ./install.sh --apply"
    if [[ "$(stat -f %Lp "$HOME_DIR")" == 700 ]]; then ok "$HOME_DIR is private (700)"
    else bad "$HOME_DIR is readable by others" "chmod 700 $HOME_DIR (the daemon also does this at start)"; fi
    if alive; then ok "daemon running (pid $(cat "$PID")), $(mode "$(ps -p "$(cat "$PID")" -o command=)")"
    else note "daemon not running (./run.sh up)"; fi
    if panel_alive; then ok "panel running (pid $(cat "$PANEL_PID"))"; else note "panel not running (./run.sh up)"; fi
    if [[ -f "$PLIST" ]]; then ok "autostart on ($PLIST)"; else note "autostart off (./run.sh autostart on)"; fi
    [[ -f "$PAUSED" ]] && note "paused by ./run.sh down (./run.sh up clears it)"
    exit "$BAD"
    ;;
  autostart)
    case "${2:-status}" in
      on)
        mkdir -p "$(dirname "$PLIST")"
        uv sync -q
        # plistlib escapes the paths. AbandonProcessGroup: "off" leaves the daemon and panel running.
        LABEL="$LABEL" PLIST="$PLIST" RUN_SH="$PWD/run.sh" LOG_OUT="$HOME_DIR/log/autostart.out" \
        .venv/bin/python - <<'PY'
import os, plistlib
env = {"PATH": os.environ["PATH"]}
if os.environ.get("AGENT_HOVER_HOME"):
    env["AGENT_HOVER_HOME"] = os.environ["AGENT_HOVER_HOME"]
with open(os.environ["PLIST"], "wb") as f:
    plistlib.dump({
        "Label": os.environ["LABEL"],
        "ProgramArguments": ["/bin/bash", os.environ["RUN_SH"], "supervise"],
        "EnvironmentVariables": env,
        "RunAtLoad": True, "KeepAlive": True, "AbandonProcessGroup": True,
        "StandardOutPath": os.environ["LOG_OUT"], "StandardErrorPath": os.environ["LOG_OUT"],
    }, f)
PY
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        launchctl bootstrap "gui/$(id -u)" "$PLIST"
        rm -f "$PAUSED"
        echo "autostart on: $PLIST"
        echo "At login (and whenever iTerm2 runs), it starts the daemon (--apply) and the panel."
        echo "macOS may ask once to let it control iTerm2: allow it. To remove: ./run.sh autostart off"
        ;;
      off)
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        rm -f "$PLIST"
        echo "autostart off: LaunchAgent removed. The daemon and panel keep running; ./run.sh down stops them."
        ;;
      status)
        if [[ -f "$PLIST" ]]; then
          echo "autostart on ($PLIST)"
          launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E "^\s+(state|pid) =" || echo "  (not loaded)"
        else
          echo "autostart off"
        fi
        ;;
      *) echo "usage: ./run.sh autostart on|off|status" >&2; exit 2 ;;
    esac
    ;;
  supervise)
    # Run by the LaunchAgent. Every 10 s: when iTerm2 runs and "down" did not pause it,
    # start the daemon and the panel if they are not running.
    echo "$(date '+%F %T') supervise started"
    while true; do
      if [[ ! -f "$PAUSED" ]] && pgrep -x iTerm2 >/dev/null; then
        alive || "$0" start --apply || true
        panel_alive || "$0" panel || true
      fi
      sleep 10
    done
    ;;
  *) sed -n '2,14p' "$0"; exit 2 ;;
esac
