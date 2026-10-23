import json
import os
import subprocess
import sys
from pathlib import Path

from agent_hover import agents, config_edit as ce, states

PROJ = Path(__file__).resolve().parent.parent
CONV = "edc67fb4-33f0-4b64-8d9b-20a70dab4fdb"
UUID = "243CB74F-A646-4BFE-A2AB-E462597C654F"


def raw(**extra):
    return {"conversationId": CONV, "workspacePaths": ["/w/proj"], "modelName": "Gemini 3.8 Flash (High)",
            "transcriptPath": f"/x/brain/{CONV}/.system_generated/logs/transcript.jsonl", **extra}


def test_agy_events_map_to_states():
    def m(event, **extra):
        return states.map_event("agy", states.agy_payload(event, raw(**extra)))
    assert m("SessionStart") == (states.IDLE, "started")
    assert m("PreInvocation") == (states.WORKING, "thinking")
    assert m("PreToolUse", toolCall={"name": "run_command", "args": {}}) == (states.WORKING, "run_command")
    assert m("PostToolUse", toolCall={"name": "view_file"}) == (states.WORKING, "view_file")
    assert m("PostInvocation") is None
    assert m("Stop", terminationReason="model_stop", fullyIdle=True) == (states.DONE, "")
    assert m("Stop", terminationReason="error", error="quota") == (states.DONE, "error")
    assert m("Stop", terminationReason="max_steps_exceeded") == (states.DONE, "max steps")
    p = states.agy_payload("Stop", raw())
    assert (p["session_id"], p["cwd"], p["model"]) == (CONV, "/w/proj", "Gemini 3.8 Flash (High)")
    assert states.agy_payload("Stop", "junk")["session_id"] is None


def run_hook(event, payload, env):
    return subprocess.run([sys.executable, "-I", str(PROJ / "agent_hover" / "hook.py"), "agy", event],
                          input=payload, capture_output=True, text=True, env=env, timeout=20)


def test_hook_always_answers_agy(tmp_path):
    env = {"PATH": os.environ["PATH"], "AGENT_HOVER_HOME": str(tmp_path)}  # no tab: an IDE session
    for event, want in [("PreToolUse", {"decision": "allow"}), ("Stop", {"decision": "stop"}),
                        ("PostToolUse", {}), ("SessionStart", {})]:
        r = run_hook(event, json.dumps(raw()), env)
        assert r.returncode == 0 and json.loads(r.stdout) == want
    r = run_hook("PreToolUse", "not json", env)  # a broken payload still gets its answer
    assert json.loads(r.stdout) == {"decision": "allow"}
    assert not (tmp_path / "state").exists()


def test_hook_writes_agy_state(tmp_path):
    env = {"PATH": os.environ["PATH"], "AGENT_HOVER_HOME": str(tmp_path), "ITERM_SESSION_ID": f"w0t0p0:{UUID}"}
    run_hook("PreToolUse", json.dumps(raw(toolCall={"name": "run_command"})), env)
    rec = json.loads((tmp_path / "state" / f"{UUID}.json").read_text())
    assert (rec["agent"], rec["state"], rec["detail"], rec["agent_session"], rec["cwd"], rec["model"]) == (
        "agy", "working", "run_command", CONV, "/w/proj", "Gemini 3.8 Flash (High)")
    run_hook("Stop", json.dumps(raw(terminationReason="model_stop")), env)
    assert json.loads((tmp_path / "state" / f"{UUID}.json").read_text())["state"] == "done"
    # While working, a Stop from another conversation (a sub-agent) does not end the turn.
    run_hook("PreToolUse", json.dumps(raw()), env)
    sub = "11111111-2222-3333-4444-555555555555"
    run_hook("SessionStart", json.dumps(raw(conversationId=sub)), env)
    run_hook("Stop", json.dumps(raw(conversationId=sub, terminationReason="model_stop")), env)
    rec = json.loads((tmp_path / "state" / f"{UUID}.json").read_text())
    assert (rec["state"], rec["agent_session"]) == ("working", CONV)
    run_hook("Stop", json.dumps(raw(terminationReason="model_stop")), env)
    # A review run (AGY_REVIEW_ACTIVE, or agy -p) in this tab does not change it.
    run_hook("PreToolUse", json.dumps(raw()), {**env, "AGY_REVIEW_ACTIVE": "1"})
    assert json.loads((tmp_path / "state" / f"{UUID}.json").read_text())["state"] == "done"


def test_agy_hooks_file_install_uninstall_restore(tmp_path, monkeypatch):
    gem = tmp_path / ".gemini" / "config"
    gem.mkdir(parents=True)
    monkeypatch.setattr(ce, "AGY_FILE", gem / "hooks.json")
    monkeypatch.setattr(ce, "CLAUDE_FILE", tmp_path / "none.json")
    monkeypatch.setattr(ce, "CODEX_FILE", tmp_path / "none.toml")
    monkeypatch.setattr(states, "BACKUP_DIR", tmp_path / "backup")
    ce.cmd_install(apply=True)  # no hooks.json yet: the install makes it
    data = json.loads((gem / "hooks.json").read_text())
    assert set(data[ce.AGY_HOOK_NAME]) == set(states.AGY_EVENTS)
    pre = data[ce.AGY_HOOK_NAME]["PreToolUse"][0]
    assert pre["matcher"] == "*" and pre["hooks"][0]["command"].endswith("agent_hover/hook.py' agy PreToolUse")
    assert data[ce.AGY_HOOK_NAME]["Stop"][0]["command"].endswith(" agy Stop")
    ce.cmd_restore(apply=True, force=False, backup=None)  # the file the install made is removed
    assert not (gem / "hooks.json").exists()
    # Another hook stays; uninstall removes only ours.
    (gem / "hooks.json").write_text(json.dumps({"mine": {"Stop": [{"command": "say hi"}]}}))
    ce.cmd_install(apply=True)
    ce.cmd_uninstall(apply=True)
    assert json.loads((gem / "hooks.json").read_text()) == {"mine": {"Stop": [{"command": "say hi"}]}}


def test_agy_conversation_from_presence_lock(monkeypatch):
    monkeypatch.setattr(agents, "AGY_PIDS", {})
    monkeypatch.setattr(agents, "open_files", lambda pid: [
        "/Users/u/.gemini/antigravity-cli/log/cli.log", f"/Users/u/.gemini/antigravity-cli/presence/{CONV}.lock"])
    assert agents.agy_conversation(71069) == CONV
    rec = agents.with_agy_session({"detail": "seen by daemon", "state": "idle"}, CONV)
    assert rec == {"detail": "", "state": "idle", "agent_session": CONV}


def test_agy_titles_from_summary_db(tmp_path, monkeypatch):
    import sqlite3
    db = tmp_path / "conversation_summaries.db"
    con = sqlite3.connect(db)
    con.execute("create table conversation_summaries (conversation_id text, title text, status text)")
    con.execute("insert into conversation_summaries values (?, ?, ?)", (CONV, "kids tech", "CASCADE_RUN_STATUS_RUNNING"))
    con.commit()
    con.close()
    monkeypatch.setattr(agents, "AGY_SUMMARIES", db)
    assert agents.agy_titles([CONV, "bad id", None]) == {CONV: "kids tech"}
    monkeypatch.setattr(agents, "AGY_SUMMARIES", tmp_path / "none.db")
    assert agents.agy_titles([CONV]) == {}


def test_agy_waiting_step(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr(agents, "AGY_CONVERSATIONS", tmp_path)
    con = sqlite3.connect(tmp_path / f"{CONV}.db")
    con.execute("create table steps (idx integer, step_type integer, status integer, metadata blob)")
    call = json.dumps({"CommandLine": "npm test", "Cwd": "/w/proj"}).encode()
    con.execute("insert into steps values (1, 15, 3, ?)", (b"\x0a\x05done",))
    con.execute("insert into steps values (2, 132, 9, ?)", (b"\x12\x0bcall_691055\x1a\x0brun_command\x22" + call + b"\x01\xff",))
    con.commit()
    con.close()
    tool, args = agents.agy_waiting_step(CONV)
    assert (tool, args) == ("run_command", {"CommandLine": "npm test", "Cwd": "/w/proj"})
    assert agents.agy_question(tool, args) == "Run this command?\nnpm test\n(in /w/proj)"
    assert agents.agy_question("write_file", {"path": "a"}) == 'Allow write_file?\n{"path": "a"}'
    con = sqlite3.connect(tmp_path / f"{CONV}.db")
    con.execute("update steps set status = 3")
    con.commit()
    con.close()
    assert agents.agy_waiting_step(CONV) is None
    assert agents.agy_waiting_step("../etc") is None
