import asyncio
import json

from agent_hover import daemon, states

TAB = "0D2B9374-01FE-45FF-B9F6-784320E7BC0B"


def test_claude_p_is_nested(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "user", "entrypoint": "sdk-cli"}) + "\n")
    assert states.nested_run("claude", {}, env={"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"})
    assert states.nested_run("claude", {"transcript_path": str(t)}, env={"CLAUDE_CODE_ENTRYPOINT": "cli"})
    t.write_text(json.dumps({"type": "user", "entrypoint": "cli"}) + "\n")
    assert not states.nested_run("claude", {"transcript_path": str(t)}, env={"CLAUDE_CODE_ENTRYPOINT": "cli"})
    assert not states.nested_run("claude", {}, env={})


def test_codex_exec_is_nested(tmp_path):
    r = tmp_path / "rollout.jsonl"
    r.write_text(json.dumps({"type": "session_meta", "payload": {"originator": "codex_exec", "source": "exec"}}) + "\n")
    assert states.nested_run("codex", {"transcript_path": str(r)})
    r.write_text(json.dumps({"type": "session_meta", "payload": {"originator": "codex_cli_rs", "source": "cli"}}) + "\n")
    assert not states.nested_run("codex", {"transcript_path": str(r)})
    assert not states.nested_run("codex", {"session_id": "not-a-uuid"})


class FakeSession:
    session_id = TAB

    def __init__(self, title):
        self.vars = {"jobPid": 73785, "processTitle": title, "commandLine": f"{title} resume",
                     "autoName": "codex", "path": "/w/kids"}

    async def async_get_variable(self, name):
        return self.vars.get(name)


class FakeTab:
    def __init__(self, title):
        self.all_sessions = [FakeSession(title)]


class FakeWindow:
    window_id = "W"

    def __init__(self, title):
        self.tabs = [FakeTab(title)]


class FakeApp:
    def __init__(self, title):
        self.terminal_windows = [FakeWindow(title)]


def test_reseed_a_tab_hidden_by_a_nested_run(tmp_path, monkeypatch):
    monkeypatch.setattr(states, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(states, "LOG_DIR", tmp_path / "log")
    wrong = {"iterm": TAB, "agent": "claude", "state": "ended", "event": "SessionEnd", "ts": 1.0}
    states.write_state(TAB, wrong)
    checked = {}
    asyncio.run(daemon.seed_agents(FakeApp("codex"), {TAB: wrong}, checked))
    rec = states.read_state(TAB)
    assert rec["agent"] == "codex" and rec["event"] == "seed" and rec["state"] != "ended"


def test_no_reseed_when_the_record_fits(tmp_path, monkeypatch):
    monkeypatch.setattr(states, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(states, "LOG_DIR", tmp_path / "log")
    good = {"iterm": TAB, "agent": "codex", "state": "working", "event": "PreToolUse", "ts": 1.0}
    states.write_state(TAB, good)
    asyncio.run(daemon.seed_agents(FakeApp("codex"), {TAB: good}, {}))
    assert states.read_state(TAB) == good
    shell = {"iterm": TAB, "agent": "claude", "state": "ended", "event": "SessionEnd", "ts": 2.0}
    states.write_state(TAB, shell)
    asyncio.run(daemon.seed_agents(FakeApp("zsh"), {TAB: shell}, {}))  # agent quit: the shell is in front
    assert states.read_state(TAB) == shell


def test_recent_end_is_not_reseeded(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(states, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(states, "LOG_DIR", tmp_path / "log")
    just_ended = {"iterm": TAB, "agent": "codex", "state": "ended", "event": "SessionEnd", "ts": time.time()}
    states.write_state(TAB, just_ended)
    asyncio.run(daemon.seed_agents(FakeApp("codex"), {TAB: just_ended}, {}))  # process not gone yet
    assert states.read_state(TAB) == just_ended


def test_seeded_tab_ends_when_the_agent_quits(tmp_path, monkeypatch):
    monkeypatch.setattr(states, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(states, "LOG_DIR", tmp_path / "log")
    seeded = daemon.seed_record(TAB, "codex", "codex", "/w", 1.0)
    states.write_state(TAB, seeded)
    checked = {TAB: 111}  # seen with an older job
    app = FakeApp("zsh")  # now the shell is in front (jobPid 73785 != 111)
    asyncio.run(daemon.seed_agents(app, {TAB: seeded}, checked))
    assert states.read_state(TAB)["state"] == "ended"
