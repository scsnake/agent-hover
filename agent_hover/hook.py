"""Agent hook: one event on stdin -> one state file. Usage: hook.py claude|codex

It prints nothing (Codex Stop hooks must print nothing or JSON) and always exits 0,
so a bug here never blocks the agent.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_hover import states  # noqa: E402

IT2 = "/Applications/iTerm.app/Contents/Resources/utilities/it2"
# iTerm2's own status words, as cc-status uses them for Claude.
IT2_STATUS = {states.WORKING: "working", states.WAITING: "waiting",
              states.DONE: "idle", states.IDLE: "idle", states.ENDED: ""}


def set_iterm_status(uuid, state):
    """Give Codex sessions the same iTerm2 status dot that cc-status gives Claude."""
    if not Path(IT2).exists():
        return
    subprocess.Popen([IT2, "session", "set-status", "--session", uuid, "--status", IT2_STATUS[state]],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def waiting_question(uuid, payload):
    """The question for the panel preview. Claude sends PermissionRequest (with the command)
    and then a Notification (only "Claude needs your permission..."): keep the better text."""
    question = states.question_text(payload)
    prev = states.read_state(uuid) or {}
    if prev.get("state") == states.WAITING and prev.get("question") and (
            question is None or payload.get("hook_event_name") == "Notification"):
        return prev["question"]
    return question


def main():
    agent = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    # A background job has no tab environment: find its tab through the session ID.
    uuid = states.iterm_session_uuid() or states.tab_for_session(
        payload.get("session_id"), payload.get("transcript_path"))
    if not uuid:
        return
    mapped = states.map_event(agent, payload)
    now = time.time()
    states.append_log("events.jsonl", {"ts": now, "agent": agent, "iterm": uuid,
                                       "event": payload.get("hook_event_name"),
                                       "tool": payload.get("tool_name"),
                                       "type": payload.get("notification_type"),
                                       "state": mapped[0] if mapped else None})
    if not mapped:
        return
    state, detail = mapped
    prev = states.read_state(uuid) or {}
    if (payload.get("hook_event_name") == "PostToolUse" and prev.get("state") == states.DONE
            and prev.get("agent") == agent):
        return  # a background command can end after Stop: the turn is still done
    record = {"iterm": uuid, "agent": agent, "state": state, "detail": detail,
              "event": payload.get("hook_event_name"), "ts": now,
              "agent_session": payload.get("session_id"), "cwd": payload.get("cwd")}
    if state == states.WAITING:
        record["question"] = waiting_question(uuid, payload)
    states.write_state(uuid, record)
    if agent == "codex":
        set_iterm_status(uuid, state)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # never break the agent
        pass
    sys.exit(0)
