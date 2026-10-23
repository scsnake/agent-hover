import json

from agent_hover import states


def ev(kind, **kw):
    return json.dumps({"type": "event_msg", "payload": {"type": kind, "turn_id": "T1", "root_turn_id": "T1", **kw}})


LIMIT = {"message": "You’ve hit your usage limit.", "codex_error_info": "usage_limit_exceeded"}


def write(path, *lines):
    path.write_text("\n".join(lines) + "\n")


def test_usage_limit_is_waiting(tmp_path):
    f = tmp_path / "r.jsonl"
    write(f, ev("task_started"), json.dumps({"type": "response_item", "payload": {"type": "message"}}),
          ev("task_complete", error=LIMIT, completed_at=100))
    turn = states.codex_last_turn(f)
    assert turn["type"] == "task_complete"
    state, detail, question = states.codex_turn_state(turn)
    assert (state, detail) == ("waiting", "usage limit") and "usage limit" in question


def test_new_turn_after_the_error_counts(tmp_path):
    f = tmp_path / "r.jsonl"
    write(f, ev("task_complete", error=LIMIT), ev("task_started"))
    assert states.codex_turn_state(states.codex_last_turn(f)) is None


def test_sub_agent_turns_are_skipped(tmp_path):
    f = tmp_path / "r.jsonl"
    sub = json.dumps({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "S9", "root_turn_id": "T1"}})
    write(f, ev("task_complete", error=LIMIT), sub)
    assert states.codex_last_turn(f)["type"] == "task_complete"


def test_other_ends():
    assert states.codex_turn_state({"type": "turn_aborted"}) == ("idle", "interrupted", None)
    assert states.codex_turn_state({"type": "task_complete", "error": {"message": "boom"}}) == ("done", "error", None)
    assert states.codex_turn_state({"type": "task_complete", "error": None}) is None  # normal end: the Stop hook
    assert states.codex_turn_state(None) is None
    assert states.codex_last_turn("/no/such/file") is None


def test_seeded_codex_tab_from_its_session_file(tmp_path):
    from agent_hover import daemon
    sid = "01a16727-88f9-7c72-85b6-30d19b5ef933"
    f = tmp_path / f"rollout-2026-10-23T13-06-46-{sid}.jsonl"
    meta = json.dumps({"type": "session_meta", "payload": {"id": sid, "cwd": "/w/kids", "source": "cli",
                                                           "thread_source": "user"}})
    write(f, meta, ev("task_started", started_at=50), ev("task_complete", error=LIMIT, completed_at=100))
    rec = daemon.seed_record("T", "codex", "codex", "/old", 1.0)
    new = daemon.with_codex_session(rec, str(f))
    assert (new["agent_session"], new["cwd"], new["state"], new["ts"]) == (sid, "/w/kids", "waiting", 100.0)
    assert new["detail"] == "usage limit" and new["question"].startswith("Codex hit")
    write(f, meta, ev("task_started", started_at=200))
    assert daemon.with_codex_session(new, str(f))["state"] == "working"
    assert daemon.with_codex_session(rec, None) == rec


def test_codex_seed_state():
    assert states.codex_seed_state({"type": "task_started"})[0] == "working"
    assert states.codex_seed_state({"type": "task_complete", "error": None})[0] == "done"
    assert states.codex_seed_state({"type": "turn_aborted"})[0] == "idle"


def limits(primary, secondary, limit_id="codex"):
    return json.dumps({"type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
        "limit_id": limit_id, "primary": primary, "secondary": secondary}}})


def test_resets_at(tmp_path):
    f = tmp_path / "r.jsonl"
    five = {"used_percent": 99.0, "window_minutes": 300, "resets_at": 1000}
    week = {"used_percent": 31.0, "window_minutes": 10080, "resets_at": 9000}
    write(f, limits(five, week), limits(None, None, "premium"))  # the empty premium entry is skipped
    assert states.codex_resets_at(f) == 1000.0
    write(f, limits(five, {**week, "used_percent": 100.0}))  # both full: wait for the later one
    assert states.codex_resets_at(f) == 9000.0
    write(f, limits({**five, "used_percent": 40.0}, {**week, "used_percent": 60.0}))  # none full: the fullest
    assert states.codex_resets_at(f) == 9000.0
    write(f, ev("task_started"))
    assert states.codex_resets_at(f) is None
