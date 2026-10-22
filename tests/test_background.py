import io
import json

from agent_hover import hook, states

TAB = "243CB74F-A646-4BFE-A2AB-E462597C654F"
OLD = "d710b21e-acff-4ae2-8e21-a0b45d378daf"
NEW = "c0b348df-bf25-4841-9495-522a07ba6561"


def setup(monkeypatch, tmp_path, continued=True):
    monkeypatch.setattr(states, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(states, "LOG_DIR", tmp_path / "log")
    states.write_state(TAB, {"iterm": TAB, "agent": "claude", "state": "done", "agent_session": OLD})
    project = tmp_path / "projects" / "-Users-me-scripts"
    project.mkdir(parents=True)
    lines = [{"type": "user", "sessionId": OLD}]
    if continued:
        lines.append({"type": "continued-in", "sessionId": OLD, "continuedInSessionId": NEW})
    (project / f"{OLD}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    return str(project / f"{NEW}.jsonl")


def test_background_job_finds_the_tab_it_came_from(monkeypatch, tmp_path):
    transcript = setup(monkeypatch, tmp_path)
    assert states.tab_for_session(NEW, transcript) == TAB
    assert states.tab_for_session("11111111-2222-3333-4444-555555555555", transcript) is None


def test_no_continued_in_line_means_no_tab(monkeypatch, tmp_path):
    transcript = setup(monkeypatch, tmp_path, continued=False)
    assert states.tab_for_session(NEW, transcript) is None


def test_hook_updates_the_tab_without_iterm_env(monkeypatch, tmp_path):
    transcript = setup(monkeypatch, tmp_path)
    monkeypatch.delenv("ITERM_SESSION_ID", raising=False)
    monkeypatch.setattr("sys.argv", ["hook.py", "claude"])
    event = {"hook_event_name": "UserPromptSubmit", "session_id": NEW, "transcript_path": transcript}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    hook.main()
    rec = states.read_state(TAB)
    assert rec["state"] == "working" and rec["agent_session"] == NEW
    # Later events match the tab by the stored session ID, with no transcript lookup.
    assert states.tab_for_session(NEW, None) == TAB
