import json

from agent_hover import search


def write_jsonl(path, records, ensure_ascii=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=ensure_ascii) + "\n" for r in records))


CLAUDE = [
    {"type": "user", "message": {"role": "user", "content": "Is the invoice plan done?"}},
    {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "invoice secret"},
        {"type": "tool_use", "name": "Bash", "input": {"command": "grep invoice"}},
        {"type": "text", "text": "Yes. The invoice plan is in plan.md."}]}},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "content": "invoice in tool output"}]}},
    {"type": "user", "message": {"role": "user", "content": "測試專案 的預算?"}},
]


def test_claude_chat_counts_only_message_text(tmp_path):
    f = tmp_path / "c.jsonl"
    write_jsonl(f, CLAUDE)
    hits, snip = search.search_chat(f, "INVOICE")
    assert hits == 2 and snip == "Yes. The invoice plan is in plan.md."


def test_escaped_non_ascii_text_is_found(tmp_path):
    f = tmp_path / "c.jsonl"
    write_jsonl(f, CLAUDE, ensure_ascii=True)  # 測試 stored as \\u6e2c\\u8a66
    assert "\\u6e2c" in f.read_text()
    assert search.search_chat(f, "測試專案")[0] == 1


def test_codex_messages(tmp_path):
    f = tmp_path / "r.jsonl"
    write_jsonl(f, [
        {"type": "session_meta", "payload": {"id": "X", "cwd": "/tmp/invoice"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
                                              "content": [{"type": "input_text", "text": "rename the invoice list"}]}},
        {"type": "response_item", "payload": {"type": "function_call", "arguments": "invoice"}},
    ])
    assert search.search_chat(f, "invoice") == (1, "rename the invoice list")


def test_snippet_is_cut_around_the_match():
    text = "a" * 100 + " needle " + "b" * 100
    s = search.snippet(text, 101, 107)
    assert s.startswith("…") and s.endswith("…") and "needle" in s and len(s) < 100


def test_search_name_folder_and_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "CLAUDE_PROJECTS", tmp_path / "projects")
    monkeypatch.setattr(search, "CODEX_SESSIONS", tmp_path / "sessions")
    write_jsonl(tmp_path / "projects" / "-Users-x" / "S1.jsonl", CLAUDE)
    write_jsonl(tmp_path / "sessions" / "2026" / "10" / "21" / "rollout-2026-10-21T10-00-00-S2.jsonl", [
        {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                              "content": [{"type": "output_text", "text": "no match here"}]}}])
    tabs = [
        {"session": "T1", "name": "Trade planner", "agent": "claude", "cwd": "/Users/x/pogo", "agent_session": "S1"},
        {"session": "T2", "name": "Invoice calc", "agent": "codex", "cwd": "/Users/x/calc", "agent_session": "S2"},
        {"session": "T3", "name": "Other", "agent": "claude", "cwd": "/Users/x/invoice-art", "agent_session": None},
    ]
    assert [(r["session"], r["where"]) for r in search.search("invoice", tabs=tabs)] == [
        ("T2", ["name"]), ("T3", ["folder"])]
    got = {r["session"]: r for r in search.search("invoice", chat=True, tabs=tabs)}
    assert got["T1"]["where"] == ["chat"] and got["T1"]["chat_hits"] == 2
    assert got["T2"]["where"] == ["name"] and got["T3"]["where"] == ["folder"]
    assert search.search("  ", tabs=tabs) == []


def test_transcript_model(tmp_path):
    claude = tmp_path / "c.jsonl"
    write_jsonl(claude, [{"type": "assistant", "message": {"model": "claude-opus-5-5", "content": []}},
                         {"type": "assistant", "message": {"model": "claude-sonnet-5-5", "content": []}}])
    assert search.transcript_model(claude) == "claude-sonnet-5-5"
    codex = tmp_path / "r.jsonl"
    write_jsonl(codex, [{"type": "session_meta", "payload": {"model_provider": "ollama"}},
                        {"type": "turn_context", "payload": {"model": "qwen3-coder"}}])
    assert search.transcript_model(codex) == "qwen3-coder (ollama)"
    empty = tmp_path / "e.jsonl"
    write_jsonl(empty, [{"type": "user"}])
    assert search.transcript_model(empty) is None and search.transcript_model(tmp_path / "none") is None


def msg(text, role="user"):
    return {"type": role, "message": {"role": role, "content": [{"type": "text", "text": text}]}}


def test_cache_reads_only_new_complete_lines(tmp_path):
    f = tmp_path / "S.jsonl"
    write_jsonl(f, [msg("first invoice")])
    assert search.search_chat(f, "invoice") == (1, "first invoice")
    with open(f, "a") as out:  # one complete line and one still being written
        out.write(json.dumps(msg("second\ninvoice")) + "\n" + json.dumps(msg("third invoice"))[:20])
    assert search.search_chat(f, "invoice") == (2, "second invoice")
    with open(f, "a") as out:
        out.write(json.dumps(msg("third invoice"))[20:] + "\n")
    assert search.search_chat(f, "invoice") == (3, "third invoice")
    text_file, _ = search._cache_files(f)
    assert text_file.read_text() == "first invoice\nsecond invoice\nthird invoice\n"


def test_cache_rebuilds_a_cut_transcript(tmp_path):
    f = tmp_path / "S.jsonl"
    write_jsonl(f, [msg("old invoice"), msg("more old invoice text")])
    assert search.search_chat(f, "invoice")[0] == 2
    write_jsonl(f, [msg("new")])
    assert search.search_chat(f, "invoice") == (0, None)
    assert search.search_chat(f, "new") == (1, "new")


def test_cache_drops_text_from_a_stopped_run(tmp_path):
    f = tmp_path / "S.jsonl"
    write_jsonl(f, [msg("one invoice")])
    search.search_chat(f, "invoice")
    text_file, _ = search._cache_files(f)
    with open(text_file, "a") as out:
        out.write("half-written invoice\n")  # text appended, index not saved
    with open(f, "a") as out:
        out.write(json.dumps(msg("two invoice")) + "\n")
    assert search.search_chat(f, "invoice") == (2, "two invoice")


def test_prune_removes_entries_of_deleted_transcripts(tmp_path):
    f = tmp_path / "S.jsonl"
    write_jsonl(f, [msg("x")])
    search.search_chat(f, "x")
    text_file, index_file = search._cache_files(f)
    assert text_file.exists() and index_file.exists()
    f.unlink()
    search.prune_cache()
    assert not text_file.exists() and not index_file.exists()


def test_cached_and_raw_search_agree(tmp_path):
    f = tmp_path / "c.jsonl"
    write_jsonl(f, CLAUDE, ensure_ascii=True)
    for kw in ("invoice", "測試專案", "plan.md", "nothing"):
        assert search.search_chat(f, kw) == search.search_chat_raw(f, kw)


def test_cache_drops_stray_text_when_nothing_is_new(tmp_path):
    f = tmp_path / "S.jsonl"
    write_jsonl(f, [msg("one invoice")])
    search.search_chat(f, "invoice")
    text_file, _ = search._cache_files(f)
    with open(text_file, "a") as out:
        out.write("half-written invoice\n")
    assert search.search_chat(f, "invoice") == (1, "one invoice")


def test_closed_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "CLAUDE_PROJECTS", tmp_path / "projects")
    monkeypatch.setattr(search, "CODEX_SESSIONS", tmp_path / "sessions")
    proj = tmp_path / "projects" / "-Users-x"
    base = {"cwd": "/w/app", "entrypoint": "cli"}
    write_jsonl(proj / "A.jsonl", [{**base, **msg("fix the invoice export")},
                                   {"type": "ai-title", "aiTitle": "Invoice export"},
                                   {"type": "custom-title", "customTitle": "My invoices"}])
    write_jsonl(proj / "LIVE.jsonl", [{**base, **msg("invoice live")}])
    write_jsonl(proj / "P.jsonl", [{"cwd": "/w", "entrypoint": "sdk-cli", **msg("invoice from claude -p")}])
    write_jsonl(proj / "OLD.jsonl", [{**base, **msg("invoice old")},
                                     {"type": "continued-in", "continuedInSessionId": "NEW"}])
    write_jsonl(proj / "SEEDED.jsonl", [{**base, **msg("invoice seeded")},
                                        {"type": "ai-title", "aiTitle": "Seeded tab"}])
    sid = "01a1636b-aa7d-7b41-b7be-a6f37ad7e4aa"
    day = tmp_path / "sessions" / "2026" / "10" / "22"
    write_jsonl(day / f"rollout-2026-10-22T10-00-00-{sid}.jsonl", [
        {"type": "session_meta", "payload": {"id": sid, "cwd": "/w/tool", "source": "cli"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "<environment_context>x</environment_context>"},
            {"type": "input_text", "text": "rename the invoice list"}]}}])
    write_jsonl(day / "rollout-2026-10-22T11-00-00-01a1636b-aa7d-7b41-b7be-a6f37ad7e4ab.jsonl", [
        {"type": "session_meta", "payload": {"cwd": "/w", "originator": "codex_exec", "source": "exec"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
                                              "content": [{"type": "input_text", "text": "invoice review"}]}}])
    tabs = [{"session": "T1", "name": "x", "agent": "claude", "cwd": "", "agent_session": "LIVE"},
            {"session": "T2", "name": "Seeded tab", "agent": "claude", "cwd": "", "agent_session": None}]
    found = search.search_closed("invoice", chat=True, days=7, tabs=tabs)
    by_id = {c["agent_session"]: c for c in found}
    assert set(by_id) == {"A", sid}
    assert by_id["A"]["name"] == "My invoices" and by_id["A"]["where"] == ["name", "chat"]
    assert by_id["A"]["resume"] == "cd /w/app && claude --resume A"
    assert by_id[sid]["name"] == "rename the invoice list"
    assert by_id[sid]["resume"] == f"cd /w/tool && codex resume {sid}"
    assert search.search_closed("app", days=7, tabs=tabs)[0]["where"] == ["folder"]
    assert search.search_closed("invoice", days=0, tabs=tabs) == []


def test_cache_rebuilds_when_the_text_file_is_gone(tmp_path):
    f = tmp_path / "S.jsonl"
    write_jsonl(f, [msg("one invoice")])
    search.search_chat(f, "invoice")
    text_file, _ = search._cache_files(f)
    text_file.unlink()
    assert search.search_chat(f, "invoice") == (1, "one invoice")
