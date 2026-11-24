import json
import os
import time

from agent_hover import states, subagents


def claude_session(tmp_path, *metas):
    transcript = tmp_path / "s.jsonl"
    folder = tmp_path / "s" / "subagents"
    folder.mkdir(parents=True)
    for i, (meta, age) in enumerate(metas):
        (folder / f"agent-{i}.meta.json").write_text(json.dumps(meta))
        log = folder / f"agent-{i}.jsonl"
        log.write_text("{}\n")
        os.utime(log, (time.time() - age, time.time() - age))
    return transcript


def test_claude_running_and_done(tmp_path):
    t = claude_session(tmp_path, ({"agentType": "Explore", "description": "find auth"}, 5),
                       ({"agentType": "fork", "description": "old", "finished": True}, 5),
                       ({"agentType": "x", "description": "crashed"}, subagents.STALE_S + 60))
    info = subagents.claude(t, time.time())
    assert [(r["kind"], r["what"]) for r in info["running"]] == [("Explore", "find auth")]
    assert info["done"] == 2


def test_claude_meta_change_is_seen(tmp_path):
    t = claude_session(tmp_path, ({"agentType": "a"}, 5))
    assert len(subagents.claude(t, time.time())["running"]) == 1
    meta = tmp_path / "s" / "subagents" / "agent-0.meta.json"
    meta.write_text(json.dumps({"agentType": "a", "finished": True}))
    os.utime(meta, (time.time() + 5, time.time() + 5))
    info = subagents.claude(t, time.time())
    assert (info["running"], info["done"], len(info["recent"])) == ([], 1, 1)


def test_claude_no_subagents(tmp_path):
    assert subagents.claude(tmp_path / "none.jsonl", time.time()) is None


def codex_file(folder, name, payload, *turns, age=5):
    path = folder / f"rollout-{name}.jsonl"
    lines = [{"type": "session_meta", "payload": payload}]
    lines += [{"type": "event_msg", "payload": {"type": t, "turn_id": "t1", "root_turn_id": "r1"}} for t in turns]
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))
    os.utime(path, (time.time() - age, time.time() - age))
    return path


def spawned(cid, root="root-1"):
    return {"id": cid, "session_id": root, "parent_thread_id": root, "source": {"subagent": {"thread_spawn": {
        "parent_thread_id": root, "depth": 1, "agent_path": "/root/fix_the_tests", "agent_nickname": "Parfit",
        "agent_role": None}}}}


ROOT = "019a0000-0000-7000-8000-00000000aaaa"


def codex_store(tmp_path, monkeypatch, **entries):
    """The subagent store as hook.py writes it: agent ID -> {type, start, stop, file, message}."""
    folder = tmp_path / "store"
    folder.mkdir(exist_ok=True)
    monkeypatch.setattr(states, "SUBAGENT_DIR", folder)
    (folder / f"codex-{ROOT}.json").write_text(json.dumps(entries))


def test_codex_subagents_from_the_hook_store(tmp_path, monkeypatch):
    now = time.time()
    a = codex_file(tmp_path, "a", spawned("a", ROOT), "task_started")
    b = codex_file(tmp_path, "b", spawned("b", ROOT), "task_started", "task_complete")
    c = codex_file(tmp_path, "c", spawned("c", ROOT), "task_started", age=subagents.STALE_S + 60)  # crashed
    d = codex_file(tmp_path, "d", spawned("d", ROOT))  # first turn record not in the tail yet
    e = codex_file(tmp_path, "e", spawned("e", ROOT), "task_started", age=30)  # stopped, no write since
    codex_store(tmp_path, monkeypatch,
                a={"type": "default", "start": now - 9, "stop": None, "file": str(a)},
                b={"type": "default", "start": now - 9, "stop": now - 5, "file": str(b)},
                c={"type": "default", "start": now - 9999, "stop": None, "file": str(c)},
                d={"type": "worker", "start": now - 9, "stop": None, "file": str(d)},
                e={"type": "default", "start": now - 60, "stop": now - 20, "file": str(e)},
                f={"type": "default", "start": now - 9, "stop": None, "file": str(tmp_path / "gone.jsonl")})
    info = subagents.codex(ROOT, now)
    assert [(r["kind"], r["what"]) for r in info["running"]] == [("Parfit", "fix the tests")] * 2
    assert info["done"] == 3  # b ended, c crashed, e stopped; f has no file


def test_codex_a_later_turn_runs_again(tmp_path, monkeypatch):
    """A SubagentStop, then the parent sends the subagent more work: it writes after the stop."""
    now = time.time()
    a = codex_file(tmp_path, "a", spawned("a", ROOT), "task_started", "task_complete", "task_started", age=2)
    codex_store(tmp_path, monkeypatch, a={"type": "default", "start": now - 60, "stop": now - 30, "file": str(a)})
    assert len(subagents.codex(ROOT, now)["running"]) == 1


def test_codex_file_found_by_name_when_the_hook_gave_none(tmp_path, monkeypatch):
    """Seen with Codex 0.172: the subagent ran, but its store entry had no file until SubagentStop."""
    now = time.time()
    child = "01a20dd0-db82-7f70-99b3-44dd38d5c31b"
    day = tmp_path / time.strftime("%Y/%m/%d", time.localtime(now))
    day.mkdir(parents=True)
    codex_file(day, f"2026-11-24T21-48-41-{child}", spawned(child, ROOT), "task_started")
    monkeypatch.setattr(subagents, "CODEX_SESSIONS", tmp_path)
    subagents._codex_files.clear()
    codex_store(tmp_path, monkeypatch, **{child: {"type": "default", "start": now - 5, "stop": None, "file": None}})
    assert [r["kind"] for r in subagents.codex(ROOT, now)["running"]] == ["Parfit"]
    subagents._codex_files.clear()
    codex_store(tmp_path, monkeypatch, **{"019a0000-0000-7000-8000-000000000bad": {"start": now, "stop": None}})
    assert subagents.codex(ROOT, now) is None  # no file anywhere: nothing to show


def test_codex_kind_from_the_hook_without_a_header(tmp_path, monkeypatch):
    now = time.time()
    a = tmp_path / "a.jsonl"
    a.write_text(json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n")
    codex_store(tmp_path, monkeypatch, a={"type": "explorer", "start": now, "stop": None, "file": str(a)})
    assert [(r["kind"], r["what"]) for r in subagents.codex(ROOT, now)["running"]] == [("explorer", "")]


def test_codex_no_store_no_subagents(tmp_path, monkeypatch):
    monkeypatch.setattr(states, "SUBAGENT_DIR", tmp_path / "none")
    assert subagents.codex(ROOT, time.time()) is None
    assert subagents.codex("../../etc/passwd", time.time()) is None


def test_overlay_keeps_a_done_tab_working_only_in_memory():
    recs = {"a": {"state": states.DONE, "ts": 1.0}, "b": {"state": states.WAITING, "ts": 1.0},
            "c": {"state": states.IDLE, "ts": 1.0}, "d": {"state": states.DONE, "ts": 1.0}}
    run = {"kind": "x", "what": ""}
    subs = {s: {"running": [run], "done": 0} for s in "abc"} | {"d": {"running": [], "done": 3}}
    out = subagents.overlay(recs, subs)
    assert (out["a"]["state"], out["a"]["ts"]) == (states.WORKING, 1.0)
    assert out["c"]["state"] == states.WORKING
    assert out["b"] is recs["b"] and out["d"] is recs["d"]  # a question still shows; none running
    assert recs["a"]["state"] == states.DONE


def test_scan_only_lists_sessions_with_subagents(tmp_path, monkeypatch):
    t = claude_session(tmp_path, ({"agentType": "a"}, 5))
    monkeypatch.setattr(subagents.search, "transcript", lambda agent, aid: t if aid == "c1" else None)
    monkeypatch.setattr(states, "SUBAGENT_DIR", tmp_path / "none")
    monkeypatch.setattr(subagents.quota, "STATUS_DIR", tmp_path / "status")
    recs = {"s1": {"agent": "claude", "agent_session": "c1"}, "s2": {"agent": "agy", "agent_session": "g"},
            "s3": {"agent": "codex", "agent_session": "x"}, "s4": {"agent": "claude"}}
    assert list(subagents.scan(recs, time.time())) == ["s1"]


def test_claude_last_tool_and_message(tmp_path):
    t = claude_session(tmp_path, ({"agentType": "Explore", "description": "find"}, 5),
                       ({"agentType": "a", "finished": True}, 50))
    log = tmp_path / "s" / "subagents" / "agent-0.jsonl"
    lines = [{"type": "assistant", "message": {"model": "claude-haiku-5-5", "content": [
                {"type": "text", "text": "Looking  at\nthe code"},
                {"type": "tool_use", "name": "Grep", "input": {"pattern": "auth", "path": "src"}}]}},
             {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x" * 50}]}},
             {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read",
                                                            "input": {"file_path": "/a/b.py"}}]}}]
    log.write_text("not json\n" + "".join(json.dumps(x) + "\n" for x in lines))
    info = subagents.claude(t, time.time())
    run = info["running"][0]
    assert (run["tool"], run["message"], run["model"]) == ("Read: /a/b.py", "Looking at the code", "claude-haiku-5-5")
    assert [r["kind"] for r in info["recent"]] == ["a"] and info["done"] == 1


def test_claude_final_report_is_the_last_message(tmp_path):
    t = claude_session(tmp_path, ({"agentType": "a", "finished": True}, 5))
    lines = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
                                                            "input": {"command": "ls"}}]}},
             {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "SubagentHandback",
                                                            "input": {"message": "All tests pass."}}]}}]
    (tmp_path / "s" / "subagents" / "agent-0.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    assert subagents._claude_last(tmp_path / "s" / "subagents" / "agent-0.jsonl") == ("Bash: ls", "All tests pass.")


def test_recent_keeps_the_newest_ended(tmp_path):
    metas = [({"agentType": f"a{i}", "finished": True}, 100 * i) for i in range(5)]
    info = subagents.claude(claude_session(tmp_path, *metas), time.time())
    assert [r["kind"] for r in info["recent"]] == ["a0", "a1", "a2"] and info["done"] == 5


def test_codex_last_tool_and_message(tmp_path):
    path = tmp_path / "rollout-x.jsonl"
    lines = [{"type": "response_item", "payload": {"type": "agent_message", "content": [{"text": "task from parent"}]}},
             {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "input": "pwd"}},
             {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                   "content": [{"type": "output_text", "text": "Done here"}]}},
             {"type": "response_item", "payload": {"type": "function_call", "name": "apply_patch", "arguments": "{}"}}]
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))
    assert subagents._codex_last(path) == ("apply_patch: {}", "Done here")
    path.write_text(json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": "End"}}) + "\n")
    assert subagents._codex_last(path) == (None, "End")


AGY_ID = "458b21a3-3f6f-4e0d-ad44-ccab8caef0c6"


def agy_status(tmp_path, monkeypatch, subs, ts):
    """A status file as quota_tee.py writes it for agy (the shape agy 1.5.16 sends)."""
    folder = tmp_path / "status"
    folder.mkdir(exist_ok=True)
    (folder / f"agy-{AGY_ID}.json").write_text(json.dumps({"model": "Gemini 4 Hydrogen (High)", "ts": ts,
                                                         "subagents": subs}))
    monkeypatch.setattr(subagents.quota, "STATUS_DIR", folder)
    monkeypatch.setattr(subagents.agents, "agy_tree", lambda: {})  # never the real agy database


def agy_sub(role, status):
    return {"name": "self", "role": role, "model": "Gemini 4 Hydrogen (High)", "status": status}


def test_agy_running_and_completed(tmp_path, monkeypatch):
    subagents._agy_seen.clear()
    now = time.time()
    agy_status(tmp_path, monkeypatch, [agy_sub("Prime Number Lister", "completed"),
                                       agy_sub("Write tests", "running")], now)
    info = subagents.agy(AGY_ID, now)
    assert [(r["kind"], r["what"], r["model"]) for r in info["running"]] == [("self", "Write tests", "Gemini 4 Hydrogen (High)")]
    assert [r["what"] for r in info["recent"]] == ["Prime Number Lister"] and info["done"] == 1
    assert info["running"][0]["tool"] is None and info["running"][0]["message"] is None


def test_agy_since_is_first_seen_and_ended_is_when_it_ended(tmp_path, monkeypatch):
    subagents._agy_seen.clear()
    t0 = time.time()
    agy_status(tmp_path, monkeypatch, [agy_sub("Prime Number Lister", "running")], t0)
    assert subagents.agy(AGY_ID, t0)["running"][0]["since"] == t0
    # agy gives the role a longer text later: still the same subagent
    agy_status(tmp_path, monkeypatch, [agy_sub("Write the first 60 prime numbers", "completed")], t0 + 30)
    info = subagents.agy(AGY_ID, t0 + 30)
    assert info["running"] == [] and info["recent"][0]["since"] == t0 and info["recent"][0]["ended"] == t0 + 30
    assert subagents.agy(AGY_ID, t0 + 60)["recent"][0]["ended"] == t0 + 30


def test_agy_ended_before_the_daemon_saw_it(tmp_path, monkeypatch):
    subagents._agy_seen.clear()
    now = time.time()
    agy_status(tmp_path, monkeypatch, [agy_sub("x", "completed")], now - 100)
    assert subagents.agy(AGY_ID, now)["recent"][0]["ended"] == now - 100


def test_agy_old_status_is_not_running(tmp_path, monkeypatch):
    subagents._agy_seen.clear()
    now = time.time()
    agy_status(tmp_path, monkeypatch, [agy_sub("x", "running")], now - subagents.STALE_S - 1)
    info = subagents.agy(AGY_ID, now)
    assert info["running"] == [] and info["done"] == 1


def test_agy_none_without_subagents(tmp_path, monkeypatch):
    agy_status(tmp_path, monkeypatch, [], time.time())
    assert subagents.agy(AGY_ID, time.time()) is None
    assert subagents.agy("458b21a3-0000-0000-0000-000000000000", time.time()) is None  # no status file
    (tmp_path / "status" / f"agy-{AGY_ID}.json").write_text(json.dumps({"model": "m", "ts": time.time()}))
    assert subagents.agy(AGY_ID, time.time()) is None  # an older agy: no "subagents" in the input


def test_scan_reads_agy_and_forgets_closed_sessions(tmp_path, monkeypatch):
    subagents._agy_seen.clear()
    monkeypatch.setattr(states, "SUBAGENT_DIR", tmp_path / "none")
    agy_status(tmp_path, monkeypatch, [agy_sub("x", "running")], time.time())
    out = subagents.scan({"s1": {"agent": "agy", "agent_session": AGY_ID}}, time.time())
    assert list(out) == ["s1"] and len(out["s1"]["running"]) == 1 and subagents._agy_seen
    subagents.scan({}, time.time())
    assert subagents._agy_seen == {}


def test_agy_running_entry_needs_the_database_once_the_input_is_old(tmp_path, monkeypatch):
    """agy can leave its last input at "running" after the subagent ended (seen with 1.5.16)."""
    subagents._agy_seen.clear()
    now = time.time()
    idle, busy = "CASCADE_RUN_STATUS_IDLE", "CASCADE_RUN_STATUS_RUNNING"
    agy_status(tmp_path, monkeypatch, [agy_sub("x", "running")], now - 60)
    ended = {AGY_ID: ("", idle), "sub1": (AGY_ID, idle)}
    assert subagents.agy(AGY_ID, now, ended)["running"] == []
    assert len(subagents.agy(AGY_ID, now, {**ended, "sub1": (AGY_ID, busy)})["running"]) == 1
    assert len(subagents.agy(AGY_ID, now, {})["running"]) == 1  # no database row: trust the input
    agy_status(tmp_path, monkeypatch, [agy_sub("x", "running")], now - 5)
    assert len(subagents.agy(AGY_ID, now, ended)["running"]) == 1  # just started: the database can lag
