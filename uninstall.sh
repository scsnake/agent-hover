#!/usr/bin/env bash
# Undo agent-hover. Dry run unless --apply.
#   ./uninstall.sh                     show what would be removed
#   ./uninstall.sh --apply             remove autostart, stop the panel and daemon, unbury its tabs,
#                                      remove only our hooks
#   ./uninstall.sh --restore-backup    show the exact backup restore (dry run)
#   ./uninstall.sh --restore-backup --apply [--force]
set -euo pipefail
cd "$(dirname "$0")"
uv sync -q
APPLY=""; MODE="uninstall"; EXTRA=()
for a in "$@"; do
  case "$a" in
    --apply) APPLY="--apply" ;;
    --restore-backup) MODE="restore" ;;
    --force) EXTRA+=(--force) ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done
PLIST="$HOME/Library/LaunchAgents/local.agent-hover.plist"
if [[ -z "$APPLY" && -f "$PLIST" ]]; then
  echo "would remove autostart: $PLIST (and stop the panel and the daemon)"
fi
if [[ -n "$APPLY" ]]; then
  # Autostart first, so it cannot start the daemon again; then the panel.
  ./run.sh autostart off
  ./run.sh panel-stop
  # Stop first. If the daemon does not stop, keep the hooks: removing them under a live
  # daemon would leave it burying tabs with no way to undo through this script.
  if ! ./run.sh stop; then
    echo "Uninstall stopped: the daemon did not stop. Nothing removed." >&2
    echo "Find it with: ps -ax | grep agent_hover.daemon   then kill it, and run this again." >&2
    exit 1
  fi
  if ! ./run.sh restore; then
    echo "Uninstall stopped: restore of buried tabs failed. Nothing removed." >&2
    echo "Check iTerm2 menu Session > Buried Sessions, then run this again." >&2
    exit 1
  fi
fi
uv run --no-sync python -m agent_hover.config_edit "$MODE" $APPLY "${EXTRA[@]+"${EXTRA[@]}"}"
