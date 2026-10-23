"""Find agent tabs by keyword: in the tab name, the agent's folder, and (optional) the chat text.

  uv run python -m agent_hover.search [--chat] [--json] KEYWORD

Only the live agent tabs (the rows of panel.json: shown or buried) are searched. The chat text
comes from the agent's own transcript (found by the session ID in the tab's state file):
Claude ~/.claude/projects/*/<id>.jsonl, Codex ~/.codex/sessions/**/rollout-*-<id>.jsonl.
Only user and agent message text counts, not thinking, tool calls or tool output.

The chat search reads a cache of that message text (~/.agent-hover/chatcache/, about 1% of the
transcript size). Transcripts only grow at the end, so each search reads only the new lines.

--closed also searches recently closed agents: interactive sessions whose transcript changed in
the last panel.closed_days days (config.json, default 7) and that no tab shows now. Their name is
the session title (Claude), or the first prompt (Codex); each comes with its resume command.
"""
import argparse
import hashlib
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

from agent_hover import states

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CODEX_SESSIONS = Path.home() / ".codex" / "sessions"
SNIPPET = 40  # characters on each side of the match
CACHE_DIR = states.HOME / "chatcache"
# A transcript line can hold message text only if it has one of these (cheap test before json.loads).
TEXT_MARKS = (b'"text"', b'"content":"', b'"content": "')
CACHE_VERSION = 2  # 2: the index also keeps the session facts (META)
# Session facts found in raw transcript bytes: the last match counts ...
META_LAST = {
    "custom_title": re.compile(rb'"customTitle":\s?"((?:[^"\\]|\\.)*)"'),  # Claude: a name you set
    "ai_title": re.compile(rb'"aiTitle":\s?"((?:[^"\\]|\\.)*)"'),
    "continued_in": re.compile(rb'"continuedInSessionId":\s?"([^"]+)"'),  # went on as a background job
}
# ... or the first one.
META_FIRST = {
    "cwd": re.compile(rb'"cwd":\s?"((?:[^"\\]|\\.)*)"'),
    "entrypoint": re.compile(rb'"entrypoint":\s?"([a-z-]+)"'),  # Claude: cli, or sdk-cli for claude -p
    "source": re.compile(rb'"source":\s?"([a-z_]+)"'),  # Codex: cli, or exec for codex exec
    "originator": re.compile(rb'"originator":\s?"([a-z_]+)"'),
}
CLOSED_LIMIT = 20


def live_tabs():
    """[{session, name, agent, cwd, agent_session}] for the rows the panel shows."""
    try:
        rows = json.loads((states.HOME / "panel.json").read_text())["rows"]
    except (OSError, ValueError, KeyError):
        rows = []
    recs = states.read_states()
    out = []
    for r in rows:
        rec = recs.get(r["session"], {})
        out.append({"session": r["session"], "name": r.get("name") or "", "agent": r.get("agent", ""),
                    "cwd": rec.get("cwd") or r.get("cwd") or "", "agent_session": rec.get("agent_session")})
    return out


def transcript(agent, agent_session):
    if not agent_session:
        return None  # a tab found by the daemon before any hook event: chat file unknown
    if agent == "claude":
        found = sorted(CLAUDE_PROJECTS.glob(f"*/{agent_session}.jsonl"))
    elif agent == "codex":
        found = sorted(CODEX_SESSIONS.glob(f"**/rollout-*{agent_session}.jsonl"))
    else:
        return None
    return found[-1] if found else None


MODEL_RE = re.compile(r'"model"\s*:\s*"([^"]{1,80})"')
PROVIDER_RE = re.compile(r'"model_provider"\s*:\s*"([^"]{1,80})"')


def transcript_model(path, tail=256 * 1024, head=32 * 1024):
    """The model a transcript used last, e.g. 'claude-opus-5-5' or 'gpt-5.6-sol (openai)'.

    Claude writes "model" in each answer; Codex writes "model" in each turn and
    "model_provider" in its first line. A custom provider shows up the same way. None if not found."""
    try:
        with open(path, "rb") as f:
            start = f.read(head).decode("utf-8", "replace")
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - tail))
            end = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    models = MODEL_RE.findall(end) or MODEL_RE.findall(start)
    if not models:
        return None
    provider = PROVIDER_RE.findall(start)
    return models[-1] + (f" ({provider[-1]})" if provider else "")


def message_texts(record):
    """User and agent message text in one transcript line (Claude or Codex format)."""
    if record.get("type") in ("user", "assistant"):  # Claude
        content = (record.get("message") or {}).get("content")
        if isinstance(content, str):
            return [content]
        if isinstance(content, list):
            return [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
    payload = record.get("payload")
    if (record.get("type") == "response_item" and isinstance(payload, dict)  # Codex
            and payload.get("type") == "message" and payload.get("role") in ("user", "assistant")):
        return [p.get("text", "") for p in payload.get("content") or [] if isinstance(p, dict)]
    return []


def keyword_pattern(keyword):
    """The keyword as typed, or as a JSON \\u escape (some files store non-ASCII text that way)."""
    escaped = json.dumps(keyword)[1:-1]
    forms = {re.escape(keyword), re.escape(escaped)}
    return re.compile("|".join(forms), re.IGNORECASE)


def snippet(text, start, end):
    s = text[max(0, start - SNIPPET):end + SNIPPET]
    s = re.sub(r"\s+", " ", s).strip()
    return ("…" if start > SNIPPET else "") + s + ("…" if end + SNIPPET < len(text) else "")


def _cache_files(path):
    key = f"{Path(path).stem[-36:]}-{hashlib.sha1(str(path).encode()).hexdigest()[:10]}"
    return CACHE_DIR / f"{key}.txt", CACHE_DIR / f"{key}.json"


def _unescape(raw):
    try:
        return json.loads(b'"' + raw + b'"')
    except ValueError:
        return raw.decode("utf-8", "replace")


def user_texts(record):
    """The person's message text in one transcript line (Claude or Codex format)."""
    payload = record.get("payload")
    if record.get("type") == "user" or (isinstance(payload, dict) and payload.get("role") == "user"):
        return message_texts(record)
    return []


def cached_text(path):
    """The cache file with the message text of one transcript, one message per line (newlines
    in a message become spaces). Reads only the transcript lines added since the last call."""
    return _update_cache(path)[0]


def _update_cache(path):
    """(cache text file, index). The index also keeps the session facts in "meta"."""
    text_file, index_file = _cache_files(path)
    st = os.stat(path)
    try:
        index = json.loads(index_file.read_text())
    except (OSError, ValueError):
        index = {}
    offset, text_size, meta = index.get("offset", 0), index.get("text_size", 0), index.get("meta", {})
    if (index.get("v") != CACHE_VERSION or index.get("path") != str(path) or index.get("inode") != st.st_ino
            or st.st_size < offset):
        offset, text_size, meta = 0, 0, {}  # new, replaced or cut transcript, or old cache: build again
    if offset == st.st_size and text_file.exists() and text_file.stat().st_size == text_size:
        return text_file, index
    states.ensure_dir(CACHE_DIR)
    lines = []
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read(st.st_size - offset)
    end = data.rfind(b"\n") + 1  # only complete lines; a line still being written waits
    for key, pattern in META_LAST.items():
        found = pattern.findall(data, 0, end)
        if found:
            meta[key] = _unescape(found[-1])
    for key, pattern in META_FIRST.items():
        m = None if key in meta else pattern.search(data, 0, end)
        if m:
            meta[key] = _unescape(m.group(1))
    for raw in data[:end].split(b"\n"):
        if not any(m in raw for m in TEXT_MARKS):
            continue
        try:
            record = json.loads(raw)
        except ValueError:
            continue
        if "first_prompt" not in meta:  # skip wrapped text such as <environment_context>
            first = next((t for t in user_texts(record) if t.strip() and not t.lstrip().startswith("<")), None)
            if first:
                meta["first_prompt"] = " ".join(first.split())[:80]
        for t in message_texts(record):
            t = " ".join(t.split())
            if t:
                lines.append(t + "\n")
    with open(text_file, "ab") as out:
        out.truncate(text_size)  # drop text from a run that stopped before it saved the index
        out.seek(text_size)
        out.write("".join(lines).encode("utf-8"))
        text_size = out.tell()
    tmp = index_file.with_suffix(f".tmp{os.getpid()}")
    index = {"v": CACHE_VERSION, "path": str(path), "inode": st.st_ino, "offset": offset + end,
             "text_size": text_size, "meta": meta}
    tmp.write_text(json.dumps(index, ensure_ascii=False))
    os.replace(tmp, index_file)
    return text_file, index


def prune_cache():
    """Remove cache entries whose transcript is gone."""
    if not CACHE_DIR.is_dir():
        return
    for index_file in CACHE_DIR.glob("*.json"):
        try:
            gone = not Path(json.loads(index_file.read_text())["path"]).exists()
        except (OSError, ValueError, KeyError, TypeError):
            gone = True
        if gone:
            index_file.with_suffix(".txt").unlink(missing_ok=True)
            index_file.unlink(missing_ok=True)


def search_chat(path, keyword):
    """(number of matching messages, snippet of the newest one) or (0, None)."""
    exact = re.compile(re.escape(keyword), re.IGNORECASE)
    try:
        text_file = cached_text(path)
    except OSError:
        return search_chat_raw(path, keyword)
    hits, last = 0, None
    with open(text_file, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = exact.search(line)
            if m:
                hits += 1
                line = line.rstrip("\n")
                last = snippet(line, m.start(), m.end())
    return hits, last


def search_chat_raw(path, keyword):
    """search_chat without the cache: read the whole transcript."""
    raw = keyword_pattern(keyword)
    exact = re.compile(re.escape(keyword), re.IGNORECASE)
    hits, last = 0, None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not raw.search(line):
                    continue  # fast skip: most lines do not contain the keyword at all
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                for text in message_texts(record):
                    m = exact.search(text)
                    if m:
                        hits += 1
                        last = snippet(text, m.start(), m.end())
    except OSError:
        return 0, None
    return hits, last


def closed_days():
    """panel.closed_days from config.json (default 7; 0: do not search closed agents)."""
    panel = states.load_config().get("panel")
    value = panel.get("closed_days", 7) if isinstance(panel, dict) else 7
    return max(0.0, float(value)) if isinstance(value, (int, float)) else 7.0


def recent_transcripts(days):
    """(agent, path, mtime) for transcripts changed in the last days."""
    cutoff = time.time() - days * 86400
    for agent, root, pattern in (("claude", CLAUDE_PROJECTS, "*/*.jsonl"),
                                 ("codex", CODEX_SESSIONS, "**/rollout-*.jsonl")):
        for p in root.glob(pattern):
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff:
                yield agent, p, mtime


def resume_command(agent, session_id, cwd):
    run = f"claude --resume {session_id}" if agent == "claude" else f"codex resume {session_id}"
    return f"cd {shlex.quote(cwd)} && {run}" if cwd else run


def closed_sessions(days, live_ids, live_names=()):
    """Recently closed agents, newest first: interactive sessions (not claude -p or codex exec)
    that no tab shows, that did not go on as a background job, and that have messages.
    live_names: (agent, tab name) of open tabs with no known session (found by the daemon before
    any hook event); iTerm2 names such a tab after the session title, so a match counts as open."""
    out = []
    for agent, path, mtime in recent_transcripts(days):
        session_id = path.stem if agent == "claude" else path.stem[-36:]
        if session_id in live_ids:
            continue
        try:
            text_file, index = _update_cache(path)
        except OSError:
            continue
        meta = index.get("meta", {})
        if (meta.get("continued_in") or index.get("text_size", 0) == 0 or meta.get("entrypoint") == "sdk-cli"
                or meta.get("source") == "exec" or meta.get("originator") == "codex_exec"):
            continue
        cwd = meta.get("cwd", "")
        name = meta.get("custom_title") or meta.get("ai_title") or meta.get("first_prompt") or Path(cwd).name
        if (agent, name) in live_names:
            continue
        out.append({"agent": agent, "agent_session": session_id, "name": name, "cwd": cwd, "last": mtime,
                    "resume": resume_command(agent, session_id, cwd), "path": path})
    return sorted(out, key=lambda c: -c["last"])


def search_closed(keyword, chat=False, days=None, tabs=None):
    """[{agent, agent_session, name, cwd, last, resume, where, snippet, chat_hits}], newest first."""
    keyword = keyword.strip()
    days = closed_days() if days is None else days
    if not keyword or days <= 0:
        return []
    tabs = live_tabs() if tabs is None else tabs
    live_ids = {t["agent_session"] for t in tabs if t["agent_session"]}
    live_names = {(t["agent"], t["name"]) for t in tabs if not t["agent_session"]}
    k = keyword.lower()
    out = []
    for c in closed_sessions(days, live_ids, live_names):
        where, snip, hits = [], None, 0
        if k in c["name"].lower():
            where.append("name")
        if k in c["cwd"].lower():
            where.append("folder")
        if chat:
            hits, snip = search_chat(c["path"], keyword)
            if hits:
                where.append("chat")
        if where:
            c = {key: v for key, v in c.items() if key != "path"}
            out.append({**c, "where": where, "snippet": snip, "chat_hits": hits})
            if len(out) >= CLOSED_LIMIT:
                break
    return out


def search(keyword, chat=False, tabs=None):
    """[{session, where: [...], snippet, chat_hits}] for the tabs that match, in panel order."""
    keyword = keyword.strip()
    if not keyword:
        return []
    k = keyword.lower()
    out = []
    if chat:
        prune_cache()
    for t in live_tabs() if tabs is None else tabs:
        where, snip, hits = [], None, 0
        if k in t["name"].lower():
            where.append("name")
        if k in t["cwd"].lower():
            where.append("folder")
        if chat:
            path = transcript(t["agent"], t["agent_session"])
            if path:
                hits, snip = search_chat(path, keyword)
                if hits:
                    where.append("chat")
        if where:
            out.append({"session": t["session"], "name": t["name"], "cwd": t["cwd"],
                        "where": where, "snippet": snip, "chat_hits": hits})
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("keyword")
    p.add_argument("--chat", action="store_true", help="also search the chat text (slower)")
    p.add_argument("--closed", action="store_true",
                   help="also search recently closed agents (panel.closed_days in config.json, default 7)")
    p.add_argument("--json", action="store_true", help="JSON output (for the panel)")
    args = p.parse_args()
    results = search(args.keyword, chat=args.chat)
    closed = search_closed(args.keyword, chat=args.chat) if args.closed else []
    if args.json:
        json.dump({"query": args.keyword, "chat": args.chat, "results": results, "closed": closed,
                   "closed_days": closed_days() if args.closed else 0}, sys.stdout, ensure_ascii=False)
        return
    for r in results:
        line = f"{r['name'] or r['session'][:8]}  [{', '.join(r['where'])}]  {r['cwd']}"
        if r["snippet"]:
            line += f"\n    {r['chat_hits']}× “{r['snippet']}”"
        print(line)
    if not results:
        print("no match")
    if args.closed:
        print(f"\nClosed (last {closed_days():g} days):" if closed else "\nClosed: no match")
        for c in closed:
            when = time.strftime("%m-%d %H:%M", time.localtime(c["last"]))
            print(f"{c['name']}  [{', '.join(c['where'])}]  {when}\n    {c['resume']}")
            if c["snippet"]:
                print(f"    {c['chat_hits']}× “{c['snippet']}”")


if __name__ == "__main__":
    main()
