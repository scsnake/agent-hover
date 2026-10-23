import json

from agent_hover import daemon

SID = "9ff5d09d-0bd1-475d-8291-6345e387acec"


def test_claude_session_file(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "CLAUDE_SESSIONS", tmp_path)
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
