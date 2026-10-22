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
