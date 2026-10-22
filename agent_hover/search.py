"""Find agent tabs by keyword: in the tab name, the agent's folder, and (optional) the chat text.

  uv run python -m agent_hover.search [--chat] [--json] KEYWORD

Only the live agent tabs (the rows of panel.json: shown or buried) are searched. The chat text
comes from the agent's own transcript (found by the session ID in the tab's state file):
Claude ~/.claude/projects/*/<id>.jsonl, Codex ~/.codex/sessions/**/rollout-*-<id>.jsonl.
Only user and agent message text counts, not thinking, tool calls or tool output.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from agent_hover import states

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CODEX_SESSIONS = Path.home() / ".codex" / "sessions"
SNIPPET = 40  # characters on each side of the match


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


def search_chat(path, keyword):
    """(number of matching messages, snippet of the newest one) or (0, None)."""
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


def search(keyword, chat=False, tabs=None):
    """[{session, where: [...], snippet, chat_hits}] for the tabs that match, in panel order."""
    keyword = keyword.strip()
    if not keyword:
        return []
    k = keyword.lower()
    out = []
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
    p.add_argument("--json", action="store_true", help="JSON output (for the panel)")
    args = p.parse_args()
    results = search(args.keyword, chat=args.chat)
    if args.json:
        json.dump({"query": args.keyword, "chat": args.chat, "results": results}, sys.stdout, ensure_ascii=False)
        return
    for r in results:
        line = f"{r['name'] or r['session'][:8]}  [{', '.join(r['where'])}]  {r['cwd']}"
        if r["snippet"]:
            line += f"\n    {r['chat_hits']}× “{r['snippet']}”"
        print(line)
    if not results:
        print("no match")


if __name__ == "__main__":
    main()
