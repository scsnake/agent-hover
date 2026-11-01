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


def test_codex_children_by_root(tmp_path, monkeypatch):
    now = time.time()
    monkeypatch.setattr(subagents, "CODEX_SESSIONS", tmp_path)
    today = tmp_path / time.strftime("%Y/%m/%d", time.localtime(now))
    today.mkdir(parents=True)
    codex_file(today, "main", {"id": "root-1", "source": "cli"}, "task_started")
    codex_file(today, "a", spawned("a"), "task_started")
    codex_file(today, "b", spawned("b"), "task_started", "task_complete")
    codex_file(today, "c", spawned("c"), "task_started", age=subagents.STALE_S + 60)  # crashed
    codex_file(today, "d", spawned("d"))  # first turn record not in the tail yet
    codex_file(today, "e", {"id": "e", "session_id": "root-2", "parent_thread_id": "root-2",
                            "source": {"subagent": "review"}}, "task_started")
    children = subagents.codex_children(now)
    assert sorted(children) == ["root-1", "root-2"]
    info = subagents.codex(children["root-1"], now)
    assert [(r["kind"], r["what"]) for r in info["running"]] == [("Parfit", "fix the tests")] * 2
    assert info["done"] == 2
    assert subagents.codex(children["root-2"], now)["running"][0]["kind"] == "review"


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


def test_scan_only_reads_claude_and_codex(tmp_path, monkeypatch):
    t = claude_session(tmp_path, ({"agentType": "a"}, 5))
    monkeypatch.setattr(subagents.search, "transcript", lambda agent, aid: t if aid == "c1" else None)
    monkeypatch.setattr(subagents, "CODEX_SESSIONS", tmp_path / "none")
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
