from agent_hover import hook, states


def test_ask_user_question_lists_options():
    q = states.question_text({"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "tool_input": {
        "questions": [{"question": "Which order?", "options": [
            {"label": "A", "description": "one list"}, {"label": "B"}]}]}})
    assert q == "Which order?\n  1. A — one list\n  2. B"


def test_permission_shows_command_and_description():
    q = states.question_text({"hook_event_name": "PermissionRequest", "tool_name": "Bash",
                              "tool_input": {"command": "rm -rf build", "description": "Clean"}})
    assert q == "Allow Bash?\nrm -rf build\nClean"


def test_codex_argv_command_is_joined():
    q = states.question_text({"hook_event_name": "PermissionRequest", "tool_name": "shell",
                              "tool_input": {"command": ["bash", "-lc", "ls"]}})
    assert q == "Allow shell?\nbash -lc ls"


def test_plan_and_limit():
    q = states.question_text({"hook_event_name": "PreToolUse", "tool_name": "ExitPlanMode",
                              "tool_input": {"plan": "x" * 3000}}, limit=100)
    assert q.startswith("Approve the plan?\nxxx") and len(q) == 100 and q.endswith("…")


def test_no_question():
    assert states.question_text({"hook_event_name": "Stop"}) is None


def test_notification_keeps_the_permission_text(monkeypatch, tmp_path):
    monkeypatch.setattr(states, "STATE_DIR", tmp_path)
    states.write_state("U", {"state": "waiting", "question": "Allow Bash?\nrm -rf build"})
    note = {"hook_event_name": "Notification", "notification_type": "permission_prompt",
            "message": "Claude needs your permission to use Bash"}
    assert hook.waiting_question("U", note) == "Allow Bash?\nrm -rf build"
    states.write_state("U", {"state": "working"})
    assert hook.waiting_question("U", note) == "Claude needs your permission to use Bash"


def test_session_uuid_must_be_a_uuid():
    good = "243CB74F-A646-4BFE-A2AB-E462597C654F"
    assert states.iterm_session_uuid({"ITERM_SESSION_ID": f"w0t0p0:{good}"}) == good
    for bad in ("w0t0p0:../../etc/x", "w0t0p0:", "no-colon", f"w0t0p0:{good}/../x"):
        assert states.iterm_session_uuid({"ITERM_SESSION_ID": bad}) is None


def test_late_post_tool_use_keeps_done(monkeypatch, tmp_path):
    import io
    import json
    monkeypatch.setattr(states, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(states, "LOG_DIR", tmp_path / "log")
    monkeypatch.setattr(states, "HOME", tmp_path)
    uuid = "243CB74F-A646-4BFE-A2AB-E462597C654F"
    monkeypatch.setenv("ITERM_SESSION_ID", f"w0t0p0:{uuid}")
    monkeypatch.setattr(hook.sys, "argv", ["hook.py", "claude"])

    def send(event):
        monkeypatch.setattr(hook.sys, "stdin", io.StringIO(json.dumps({"hook_event_name": event, "tool_name": "Bash"})))
        hook.main()
        return states.read_state(uuid)["state"]

    assert send("Stop") == "done"
    assert send("PostToolUse") == "done"   # late background tool end: still done
    assert send("PreToolUse") == "working"  # new work
    send("Stop")
    states.write_state(uuid, {**states.read_state(uuid), "state": "idle", "event": "seed"})  # a daemon restart
    assert send("PostToolUse") == "idle"
    assert send("PermissionRequest") == "waiting"
    assert send("PostToolUse") == "working"  # approved: the turn goes on


def test_codex_app_server_hooks_find_the_tab(monkeypatch, tmp_path):
    import io
    import json
    import time
    monkeypatch.setattr(states, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(states, "LOG_DIR", tmp_path / "log")
    monkeypatch.setattr(states, "HOME", tmp_path)
    a, b, c, wrong = ("A" * 8 + "-1111-1111-1111-" + "1" * 12, "B" * 8 + "-2222-2222-2222-" + "2" * 12,
                      "C" * 8 + "-3333-3333-3333-" + "3" * 12, "D" * 8 + "-4444-4444-4444-" + "4" * 12)
    old = "01a18029-0fa9-72f0-90e9-67fadc24a1b5"
    rows = [{"session": a, "agent": "codex", "cwd": "/w"}, {"session": b, "agent": "codex", "cwd": "/w"},
            {"session": c, "agent": "codex", "cwd": "/other"}, {"session": "gpu:tmux-1-4", "agent": "codex",
                                                                 "cwd": "/w", "host": "gpu"}]
    (tmp_path / "panel.json").write_text(json.dumps({"ts": time.time(), "rows": rows}))
    states.write_state(a, {"agent": "codex", "state": "idle", "agent_session": old})
    states.write_state(b, {"agent": "codex", "state": "idle", "agent_session": None})
    assert states.codex_tab(old, "/w") == a           # the tab that names the session
    assert states.codex_tab("new", "/w") == b         # same folder: the tab with no session yet
    assert states.codex_tab("new", "/other") == c     # the only tab in that folder
    assert states.codex_tab("new", "/none") is None
    states.write_state(a, {"agent": "codex", "state": "idle", "agent_session": None})
    assert states.codex_tab("new", "/w") is None      # two new tabs in one folder: no guess

    # The hook under the app-server: its $ITERM_SESSION_ID is the tab that started the daemon.
    states.write_state(a, {"agent": "codex", "state": "idle", "agent_session": old})
    monkeypatch.setenv("ITERM_SESSION_ID", f"w0t0p0:{wrong}")
    monkeypatch.setattr(hook, "from_app_server", lambda: True)
    monkeypatch.setattr(hook, "set_iterm_status", lambda uuid, state: None)
    monkeypatch.setattr(hook.sys, "argv", ["hook.py", "codex"])
    new = "01a18030-0000-7000-8000-000000000001"
    for event, sid in (("UserPromptSubmit", old), ("SessionStart", new), ("UserPromptSubmit", new)):
        monkeypatch.setattr(hook.sys, "stdin", io.StringIO(json.dumps(
            {"hook_event_name": event, "session_id": sid, "cwd": "/w", "prompt": "x"})))
        hook.main()
    assert states.read_state(a)["agent_session"] == old and states.read_state(a)["state"] == "working"
    assert states.read_state(b)["agent_session"] == new and states.read_state(b)["state"] == "working"
    assert states.read_state(wrong) is None
