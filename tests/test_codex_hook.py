import io
import json

from agent_hover import hook, states

UUID = "243CB74F-A646-4BFE-A2AB-E462597C654F"
SID = "019a0000-0000-7000-8000-000000000001"


def setup(monkeypatch, tmp_path, version="0.171.0"):
    monkeypatch.setattr(states, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(states, "LOG_DIR", tmp_path / "log")
    monkeypatch.setattr(states, "HOME", tmp_path)
    monkeypatch.setenv("ITERM_SESSION_ID", f"w0t0p0:{UUID}")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr(hook, "from_app_server", lambda: False)
    monkeypatch.setattr(hook.sys, "argv", ["hook.py", "codex"])
    calls = []
    monkeypatch.setattr(hook, "set_iterm_status", lambda uuid, state: calls.append(state))
    transcript = tmp_path / "rollout.jsonl"
    meta = {"type": "session_meta", "payload": {"id": SID, "cwd": "/repo", "cli_version": version} if version
            else {"id": SID, "cwd": "/repo"}}
    transcript.write_text(json.dumps(meta, separators=(",", ":")) + "\n")
    return calls, transcript


def send(monkeypatch, transcript, event, cwd, **extra):
    payload = {"hook_event_name": event, "session_id": SID, "cwd": cwd, "transcript_path": str(transcript),
               "tool_name": "shell", "tool_input": {"command": ["ls"]}, "prompt": "x", **extra}
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO(json.dumps(payload)))
    hook.main()
    return states.read_state(UUID)


def test_codex_tool_events_keep_the_session_folder(monkeypatch, tmp_path):
    calls, t = setup(monkeypatch, tmp_path)
    assert send(monkeypatch, t, "UserPromptSubmit", "/repo")["cwd"] == "/repo"
    # Codex 0.166.0: a command run with another workdir gives that folder as "cwd"
    assert send(monkeypatch, t, "PreToolUse", "/repo/sub")["cwd"] == "/repo"
    assert send(monkeypatch, t, "PermissionRequest", "/tmp")["cwd"] == "/repo"
    assert send(monkeypatch, t, "PostToolUse", "/tmp")["cwd"] == "/repo"
    assert send(monkeypatch, t, "Stop", "/repo2")["cwd"] == "/repo2"  # a session event: its folder


def test_codex_tool_event_cwd_is_used_only_when_nothing_else_is_known(monkeypatch, tmp_path):
    calls, t = setup(monkeypatch, tmp_path)
    assert send(monkeypatch, t, "PreToolUse", "/repo/sub")["cwd"] == "/repo/sub"


def test_claude_tool_events_still_set_the_folder(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(hook.sys, "argv", ["hook.py", "claude"])
    monkeypatch.setattr(hook.states, "tab_for_session", lambda *a: None)
    for event, cwd in (("UserPromptSubmit", "/repo"), ("PreToolUse", "/repo/sub")):
        monkeypatch.setattr(hook.sys, "stdin", io.StringIO(json.dumps(
            {"hook_event_name": event, "session_id": "c1", "cwd": cwd, "tool_name": "Bash"})))
        hook.main()
    assert states.read_state(UUID)["cwd"] == "/repo/sub"  # Claude's cwd is the session's (it follows cd)


def test_app_server_routing_ignores_a_tool_event_folder(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    seen = []
    monkeypatch.setattr(hook, "from_app_server", lambda: True)
    monkeypatch.setattr(hook.states, "codex_tab", lambda sid, cwd: seen.append(cwd))
    t = tmp_path / "rollout.jsonl"
    send_raw = {"session_id": SID, "transcript_path": str(t), "tool_name": "shell"}
    for event in ("UserPromptSubmit", "PreToolUse"):
        monkeypatch.setattr(hook.sys, "stdin", io.StringIO(json.dumps(
            {**send_raw, "hook_event_name": event, "cwd": "/w" if event == "UserPromptSubmit" else "/tmp"})))
        hook.main()
    assert seen == ["/w", None]


def test_new_codex_sets_its_own_iterm_status(monkeypatch, tmp_path):
    calls, t = setup(monkeypatch, tmp_path, "0.171.0")
    send(monkeypatch, t, "UserPromptSubmit", "/repo")
    assert calls == []


def test_old_or_unknown_codex_gets_the_status_from_the_hook(monkeypatch, tmp_path):
    for version in ("0.162.9", None):
        calls, t = setup(monkeypatch, tmp_path, version)
        send(monkeypatch, t, "UserPromptSubmit", "/repo")
        assert calls == ["working"], version
    calls, t = setup(monkeypatch, tmp_path)
    send(monkeypatch, tmp_path / "missing.jsonl", "UserPromptSubmit", "/repo")
    assert calls == ["working"]


def test_codex_in_tmux_gets_the_status_from_the_hook(monkeypatch, tmp_path):
    calls, t = setup(monkeypatch, tmp_path, "0.171.0")
    monkeypatch.setenv("TMUX", "/tmp/tmux-501/default,1,0")
    send(monkeypatch, t, "UserPromptSubmit", "/repo")
    assert calls == ["working"]  # Codex sends no iTerm2 status through a multiplexer


def test_codex_version_is_none_without_a_file():
    assert hook.codex_version(None) is None
