"""Subagents of an agent session, for the panel: which run now, and what they do. Read only from
the files the agents write themselves; no request, no hook, no install.

  Claude: <transcript without .jsonl>/subagents/agent-<id>.meta.json (type and description; it
          gets "finished": true at the end) and the agent-<id>.jsonl beside it.
  Codex:  each subagent thread has its own session file. Its first line names the root session,
          its last turn record says if the thread still runs. Only the session folders of today
          and yesterday are read, so "done" can be less than all for an older session.

A subagent that crashed never says that it ended, so one with no write for STALE_S is not running.
Each running subagent and the last RECENT that ended also get their model, last tool and last
message (for the panel's subagent frame), from the end of their log.
"""
import json
import time
from pathlib import Path

from agent_hover import search, states

STALE_S = 15 * 60
HEAD_BYTES = 1 << 20    # a Codex session file's first line holds the agent's whole instructions
TAIL_BYTES = 1 << 16
MAX_TEXT = 80
MAX_TOOL = 160
MAX_MESSAGE = 600
RECENT = 3
CODEX_SESSIONS = search.CODEX_SESSIONS

_metas = {}     # meta file -> (mtime, data)
_headers = {}   # Codex session file -> (root session, kind, what), or None for a main thread
_paths = {}     # Claude session -> transcript
_lasts = {}     # log file -> (mtime, (last tool, last message))


def _short(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _lines_back(path):
    """The last lines of a log as records, newest first (a cut first line is left out)."""
    for line in reversed(states._file_part(path, TAIL_BYTES, tail=True).splitlines()):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            yield rec


def _claude_last(path):
    tool = message = None
    for rec in _lines_back(path):
        content = (rec.get("message") or {}).get("content") if rec.get("type") == "assistant" else None
        for item in reversed(content) if isinstance(content, list) else []:
            args = item.get("input") if isinstance(item.get("input"), dict) else {}
            if item.get("name") == "SubagentHandback":  # its final report, not work
                message = message or args.get("message")
            elif item.get("type") == "tool_use" and tool is None:
                value = next((args[k] for k in ("description", "command", "pattern", "query", "file_path",
                                                "url", "prompt") if args.get(k)), "")
                tool = f"{item.get('name')}: {value}" if value else str(item.get("name"))
            elif item.get("type") == "text" and message is None and str(item.get("text") or "").strip():
                message = item["text"]
        if tool is not None and message is not None:
            break
    return tool, message


def _codex_last(path):
    tool = message = None
    for rec in _lines_back(path):
        payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        kind = payload.get("type")
        if rec.get("type") == "event_msg" and kind == "task_complete" and message is None:
            message = payload.get("last_agent_message")
        elif rec.get("type") != "response_item":
            continue
        elif kind in ("custom_tool_call", "function_call") and tool is None:
            tool = f"{payload.get('name')}: {payload.get('input') or payload.get('arguments') or ''}"
        elif kind == "message" and payload.get("role") == "assistant" and message is None:
            parts = [c.get("text") for c in payload.get("content") or [] if isinstance(c, dict)]
            message = "\n".join(t for t in parts if t) or None
        if tool is not None and message is not None:
            break
    return tool, message


def _detail(found, reader):
    """The panel entry of one subagent. found: (kind, what, stat, log file)."""
    kind, what, st, path = found
    cached = _lasts.get(path)
    if not cached or cached[0] != st.st_mtime:
        cached = _lasts[path] = st.st_mtime, reader(path)
    tool, message = cached[1]
    return {"kind": str(kind)[:40], "what": _short(what, MAX_TEXT),
            "since": getattr(st, "st_birthtime", st.st_mtime), "ended": st.st_mtime,
            "model": search.transcript_model(path), "tool": _short(tool, MAX_TOOL), "message": _short(message, MAX_MESSAGE)}


def _info(running, ended, reader):
    """{running: [...], recent: [...], done: count} from (kind, what, stat, log file) lists, or None."""
    if not running and not ended:
        return None
    ended = sorted(ended, key=lambda f: f[2].st_mtime, reverse=True)
    return {"running": sorted((_detail(f, reader) for f in running), key=lambda r: r["since"]),
            "recent": [_detail(f, reader) for f in ended[:RECENT]], "done": len(ended)}


def _meta(path):
    try:
        mtime = path.stat().st_mtime
        if _metas.get(path, (None,))[0] != mtime:
            data = json.loads(path.read_text())
            _metas[path] = (mtime, data if isinstance(data, dict) else {})
    except (OSError, ValueError):
        return None
    return _metas[path][1]


def claude(transcript, now):
    """{running, recent, done} of a Claude session, or None."""
    folder = Path(transcript).with_suffix("") / "subagents"
    running, ended = [], []
    for path in folder.rglob("*.meta.json") if folder.is_dir() else []:
        meta = _meta(path)
        log = path.with_name(path.name[: -len(".meta.json")] + ".jsonl")
        try:
            st = log.stat()
        except OSError:
            continue  # just started
        if meta is None:
            continue
        found = (meta.get("agentType") or "agent", meta.get("description"), st, log)
        (running if not meta.get("finished") and now - st.st_mtime < STALE_S else ended).append(found)
    return _info(running, ended, _claude_last)


def _codex_header(path):
    """(root session, kind, what) from the first line of a subagent's session file, else None."""
    if path in _headers:
        return _headers[path]
    try:
        with open(path, "rb") as f:
            line = f.readline(HEAD_BYTES)
    except OSError:
        return None
    if not line.endswith(b"\n"):
        if len(line) < HEAD_BYTES:
            return None  # being written: look again at the next scan
        _headers[path] = None
        return None
    found = None
    try:
        payload = json.loads(line).get("payload") or {}
        source = payload.get("source")
        sub = source.get("subagent") if isinstance(source, dict) else None
        root = payload.get("session_id") or payload.get("parent_thread_id")
        if sub and root and root != payload.get("id"):
            spawn = sub.get("thread_spawn") if isinstance(sub, dict) else None
            if isinstance(spawn, dict):
                what = str(spawn.get("agent_path") or "").rsplit("/", 1)[-1].replace("_", " ")
                found = root, spawn.get("agent_role") or spawn.get("agent_nickname") or "agent", what
            else:
                found = root, str(sub), ""
    except (ValueError, AttributeError):
        pass
    _headers[path] = found
    return found


def codex_children(now):
    """Root Codex session -> [(session file, kind, what)] of recent subagent threads."""
    out = {}
    for days in (0, 1):
        folder = CODEX_SESSIONS / time.strftime("%Y/%m/%d", time.localtime(now - days * 86400))
        for path in folder.glob("rollout-*.jsonl"):
            head = _codex_header(path)
            if head:
                out.setdefault(head[0], []).append((path, head[1], head[2]))
    return out


def codex(children, now):
    """{running, recent, done} of a Codex session from its subagent threads, or None."""
    running, ended = [], []
    for path, kind, what in children:
        try:
            st = path.stat()
        except OSError:
            continue
        turn = None
        if now - st.st_mtime < STALE_S:
            turn = states.codex_last_turn(path, TAIL_BYTES, main_only=False)
            turn = turn or {"type": "task_started"}  # a long turn: its start is before the tail
        (running if turn and turn.get("type") == "task_started" else ended).append((kind, what, st, path))
    return _info(running, ended, _codex_last)


def scan(recs, now):
    """iTerm session -> {running, recent, done}, for the Claude and Codex records given."""
    out, children = {}, None
    for sid, rec in recs.items():
        agent, aid = rec.get("agent"), rec.get("agent_session")
        info = None
        if aid and agent == "claude":
            path = _paths.get(aid) or search.transcript("claude", aid)
            if path:
                _paths[aid] = path
                info = claude(path, now)
        elif aid and agent == "codex":
            children = codex_children(now) if children is None else children
            info = codex(children.get(aid, []), now)
        if info:
            out[sid] = info
    return out


def overlay(recs, subs):
    """The records with a done or idle tab as working while its subagents run: the main agent
    ended its turn, but the work goes on and it will come back. The state files keep the real
    state, so this never outlives the subagents."""
    out = dict(recs)
    for sid, info in subs.items():
        rec = recs.get(sid)
        if info["running"] and rec and rec.get("state") in (states.DONE, states.IDLE):
            out[sid] = {**rec, "state": states.WORKING}
    return out
