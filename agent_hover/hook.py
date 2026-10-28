"""Agent hook: one event on stdin -> one state file. Usage: hook.py claude|codex|agy [EVENT]

It prints nothing (Codex Stop hooks must print nothing or JSON) and always exits 0, so a bug
here never blocks the agent. For agy it always prints the JSON answer agy needs ("allow" for
PreToolUse: agy's own permission rules still decide).
"""
import json
import os
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


def from_app_server():
    """True when this hook runs under `codex app-server` (not under the codex in a tab)."""
    from agent_hover import commands
    for pid in commands.parent_pids(os.getppid(), depth=4):
        argv = commands.process_argv(pid) or []
        if argv and Path(argv[0]).name == "codex" and "app-server" in argv[1:3]:
            return True
    return False


def main():
    agent = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    if (agent in ("claude", "codex") and payload.get("hook_event_name") in ("SessionStart", "UserPromptSubmit")
            and payload.get("session_id")):
        try:  # its command line, so that a resume from the panel repeats the options
            from agent_hover import commands
            if payload["hook_event_name"] == "SessionStart" or payload["session_id"] not in commands.load():
                commands.remember_from_hook(agent, payload["session_id"], os.getppid())
        except Exception:  # noqa: BLE001 - never break the agent
            pass
    if agent == "agy":
        payload = states.agy_payload(sys.argv[2] if len(sys.argv) > 2 else "", payload)
    if agent == "codex" and from_app_server():
        # Codex can run its hooks in its app-server daemon: its environment is the one of the tab
        # that started the daemon, so find the tab through the session ID or the folder instead.
        uuid = states.codex_tab(payload.get("session_id"), payload.get("cwd"))
    else:
        # A background job has no tab environment: find its tab through the session ID.
        uuid = states.iterm_session_uuid() or (None if agent == "agy" else states.tab_for_session(
            payload.get("session_id"), payload.get("transcript_path")))
    if not uuid:
        if agent == "codex":
            states.append_log("events.jsonl", {"ts": time.time(), "agent": agent, "event": payload.get("hook_event_name"),
                                               "session": payload.get("session_id"), "unrouted": True})
        return
    mapped = states.map_event(agent, payload)
    nested = states.nested_run(agent, payload)
    now = time.time()
    states.append_log("events.jsonl", {"ts": now, "agent": agent, "iterm": uuid,
                                       "event": payload.get("hook_event_name"),
                                       "tool": payload.get("tool_name"),
                                       "type": payload.get("notification_type"),
                                       **({"session": payload.get("session_id")} if agent == "agy" else {}),
                                       "state": mapped[0] if mapped else None,
                                       **({"nested": True} if nested else {})})
    if not mapped or nested:
        return
    state, detail = mapped
    prev = states.read_state(uuid) or {}
    if (payload.get("hook_event_name") == "PostToolUse" and prev.get("state") in (states.DONE, states.IDLE)
            and prev.get("agent") == agent):
        return  # a background command can end after Stop (or after a restart seeded "idle"): no turn
    if agent == "agy" and payload.get("session_id"):
        # A sub-agent has its own conversation and fires the same hooks: report the main one.
        from agent_hover import agents
        tree = agents.agy_tree()
        root, sub = agents.agy_root(payload["session_id"], tree)
        if sub and payload.get("hook_event_name") in ("Stop", "SessionStart"):
            return  # a sub-agent started or ended: the main turn goes on
        payload["session_id"] = root
        # A sub-agent that agy has not listed yet: while the tab works, keep its main conversation.
        main = agents.agy_root(prev["agent_session"], tree)[0] if prev.get("agent_session") else None
        if (prev.get("agent") == "agy" and prev.get("state") == states.WORKING and main
                and root != main and root not in tree):
            if payload.get("hook_event_name") in ("Stop", "SessionStart"):
                return
            payload["session_id"] = main
    record = {"iterm": uuid, "agent": agent, "state": state, "detail": detail,
              "event": payload.get("hook_event_name"), "ts": now,
              "agent_session": payload.get("session_id"), "cwd": payload.get("cwd") or prev.get("cwd")}
    if agent == "agy":  # agy names its model and transcript in every event
        record.update(model=payload.get("model") or prev.get("model"), transcript=payload.get("transcript_path"))
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
    if sys.argv[1:2] == ["agy"]:  # agy needs a decision for PreToolUse and Stop, also after an error
        print(json.dumps(states.AGY_REPLY.get(sys.argv[2] if len(sys.argv) > 2 else "", {})))
    sys.exit(0)
