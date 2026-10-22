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
