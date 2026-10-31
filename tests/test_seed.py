import json

from agent_hover import agents, daemon

SID = "9ff5d09d-0bd1-475d-8291-6345e387acec"


def test_claude_session_file(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "CLAUDE_SESSIONS", tmp_path)
    (tmp_path / "123.json").write_text(json.dumps({"pid": 123, "sessionId": SID}))
    (tmp_path / "456.json").write_text(json.dumps({"pid": 999, "sessionId": SID}))  # stale: other process
    assert daemon.claude_session(123)["sessionId"] == SID
    assert daemon.claude_session(456) is None
    assert daemon.claude_session(None) is None and daemon.claude_session("../x") is None


def test_seeded_record_takes_session_folder_and_state():
    rec = daemon.seed_record("T", "claude", "✳ My project", "/old", 100.0)
    assert rec["state"] == "idle" and rec["agent_session"] is None
    new = daemon.with_claude_session(rec, {"sessionId": SID, "cwd": "/w/app", "status": "waiting",
                                           "statusUpdatedAt": 150000}, 200.0)
    assert (new["agent_session"], new["cwd"], new["state"], new["ts"]) == (SID, "/w/app", "waiting", 150.0)
    assert new["event"] == "seed"  # still a seeded record: hooks replace it
    same = daemon.with_claude_session(new, {"sessionId": SID, "cwd": "/w/app", "status": "waiting"}, 300.0)
    assert same == new  # no change: no write
    assert daemon.with_claude_session(rec, {"sessionId": "../../x", "status": "nope"}, 1.0)["agent_session"] is None


class Sess:
    def __init__(self, sid, pid, title="claude", cmd="claude --resume x"):
        self.session_id = sid
        self.vars = {"jobPid": pid, "processTitle": title, "commandLine": cmd, "autoName": "✳ Blind zone",
                     "path": "/w/game"}

    async def async_get_variable(self, name):
        return self.vars.get(name)


class Win:
    def __init__(self, sessions):
        tab = type("T", (), {"all_sessions": sessions})()
        self.tabs, self.window_id = [tab], "w1"


def test_tab_seeded_again_when_its_state_file_is_gone(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setattr(daemon.states, "STATE_DIR", tmp_path)
    monkeypatch.setattr(daemon, "claude_session", lambda pid: None)
    app = type("A", (), {"terminal_windows": [Win([Sess("S", 50), Sess("Z", 60, title="zsh", cmd="-zsh")])]})()
    checked = {}
    asyncio.run(daemon.seed_agents(app, daemon.states.read_states(), checked))
    assert (tmp_path / "S.json").exists() and not (tmp_path / "Z.json").exists()
    asyncio.run(daemon.seed_agents(app, daemon.states.read_states(), checked))  # the seeded record is kept
    (tmp_path / "S.json").unlink()  # lost (an old prune deleted the files of idle tabs)
    asyncio.run(daemon.seed_agents(app, daemon.states.read_states(), checked))
    assert (tmp_path / "S.json").exists()
    assert checked["Z"] == ("no agent", 60)  # a shell tab is not read again until its job changes


def test_prune_keeps_live_tabs(tmp_path, monkeypatch):
    import os

    monkeypatch.setattr(daemon.states, "STATE_DIR", tmp_path)
    for sid in ("live", "closed", "fresh"):
        (tmp_path / f"{sid}.json").write_text("{}")
    for sid in ("live", "closed"):
        os.utime(tmp_path / f"{sid}.json", (1000, 1000))
    gone = daemon.states.prune_states({"live"}, now=1000 + 3 * 86400)
    assert gone == ["closed"] and sorted(p.stem for p in tmp_path.glob("*.json")) == ["fresh", "live"]


def test_hook_record_ends_when_agent_is_gone(tmp_path, monkeypatch):
    import asyncio
    import time

    monkeypatch.setattr(daemon.states, "STATE_DIR", tmp_path)
    shell = Sess("S", 70, title="zsh", cmd="-zsh")
    app = type("A", (), {"terminal_windows": [Win([shell])]})()
    rec = {"iterm": "S", "agent": "claude", "state": "idle", "event": "Notification", "ts": time.time() - 5}
    daemon.states.write_state("S", rec)
    checked = {}
    asyncio.run(daemon.seed_agents(app, daemon.states.read_states(), checked))
    assert daemon.states.read_state("S")["state"] == "idle"  # too fresh: maybe the agent only paused
    daemon.states.write_state("S", {**rec, "ts": time.time() - daemon.AGENT_GONE_S - 5})
    asyncio.run(daemon.seed_agents(app, daemon.states.read_states(), checked))
    new = daemon.states.read_state("S")
    assert (new["state"], new["event"]) == ("ended", "agent_gone")
