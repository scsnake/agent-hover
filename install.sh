#!/usr/bin/env bash
# Add the agent-hover hooks to Claude Code, Codex and agy (Antigravity CLI). Dry run unless --apply.
#   ./install.sh            show the planned change, write nothing
#   ./install.sh --apply    back up both files, then add the hooks
set -euo pipefail
cd "$(dirname "$0")"
uv sync -q
uv run --no-sync python -m agent_hover.config_edit install "$@"
if [[ "${1:-}" == "--apply" ]]; then
  cat <<'MSG'

Next:
  1. Claude Code: start a new session (an open session may keep its old hooks).
  2. Codex: start codex, type /hooks, and trust the new agent-hover hooks (Codex skips
     hooks that are not trusted).
  3. Start the daemon:  ./run.sh start            (dry run: logs only)
                        ./run.sh start --apply    (really buries and unburies)
To undo: ./uninstall.sh  (see README.md)
MSG
fi
