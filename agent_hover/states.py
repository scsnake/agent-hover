"""Agent hook events -> session state, and the on-disk state files.

Stdlib only: the hook imports this on every agent event, so it must load fast.
"""
import json
import os
import re
import time
from pathlib import Path

HOME = Path(os.environ.get("AGENT_HOVER_HOME", Path.home() / ".agent-hover"))
STATE_DIR = HOME / "state"
LOG_DIR = HOME / "log"
BACKUP_DIR = HOME / "backup"
BURIED_FILE = HOME / "buried.json"
PID_FILE = HOME / "daemon.pid"
CONFIG_FILE = HOME / "config.json"

WORKING, WAITING, DONE, IDLE, ENDED = "working", "waiting", "done", "idle", "ended"

# Claude Code Notification types that mean "the person must act now".
CLAUDE_WAITING_NOTIFICATIONS = {"permission_prompt", "elicitation_dialog", "agent_needs_input"}
# Claude Code tools that open a dialog for the person.
CLAUDE_DIALOG_TOOLS = {"AskUserQuestion", "ExitPlanMode"}


def map_event(agent, payload):
    """Return (state, detail) for one hook event, or None to keep the old state."""
    event = payload.get("hook_event_name", "")
    tool = payload.get("tool_name", "")
    if event == "SessionStart":
        if payload.get("source") == "compact":
            return None  # compaction can happen inside a running turn: keep the state
        return IDLE, "started"
    if event == "SessionEnd":
        return ENDED, payload.get("reason", "")
    if event == "UserPromptSubmit":
        return WORKING, "prompt"
    if event == "PermissionRequest":
        return WAITING, f"approve {tool}".strip()
    if event == "PreToolUse":
        if agent == "claude" and tool in CLAUDE_DIALOG_TOOLS:
            return WAITING, "question" if tool == "AskUserQuestion" else "plan"
        return WORKING, tool
    if event == "PostToolUse":
        return WORKING, tool
    if event == "Stop":
        return DONE, ""
    if event == "StopFailure":
        return DONE, "error"
    if event == "Interrupt":
        # The person pressed Esc, so they are at the tab already.
        return IDLE, "interrupted"
    if event == "Notification" and agent == "claude":
        kind = payload.get("notification_type", "")
        if kind in CLAUDE_WAITING_NOTIFICATIONS:
            return WAITING, kind
        if not kind and "permission" in payload.get("message", "").lower():
            return WAITING, "permission_prompt"
    return None


def _text(value):
    """A command can be a string (Claude) or an argv list (Codex)."""
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value) if value else ""


def question_text(payload, limit=1500):
    """What a waiting agent asks, for the panel preview. None if the event has no question."""
    event = payload.get("hook_event_name", "")
    tool = payload.get("tool_name", "")
    inp = payload.get("tool_input")
    inp = inp if isinstance(inp, dict) else {}
    lines = []
    if tool == "AskUserQuestion":
        for q in inp.get("questions") or []:
            if not isinstance(q, dict):
                continue
            lines.append(q.get("question", ""))
            for i, o in enumerate(q.get("options") or [], 1):
                label, desc = (o.get("label", ""), o.get("description", "")) if isinstance(o, dict) else (o, "")
                lines.append(f"  {i}. {label}" + (f" — {desc}" if desc else ""))
    elif tool == "ExitPlanMode":
        lines += ["Approve the plan?", _text(inp.get("plan"))]
    elif event == "PermissionRequest":
        lines.append(f"Allow {tool or 'tool'}?")
        for key in ("command", "cmd", "file_path", "path", "url", "pattern", "query"):
            if inp.get(key):
                lines.append(_text(inp[key]))
                break
        else:
            if inp:
                lines.append(json.dumps(inp, ensure_ascii=False)[:400])
        if inp.get("description"):
            lines.append(_text(inp["description"]))
    elif event == "Notification" and payload.get("message"):
        lines.append(payload["message"])
    text = "\n".join(line for line in lines if line).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def load_config():
    """~/.agent-hover/config.json as a dict ({} if missing or broken). See config.example.json."""
    try:
        data = json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


UUID_RE = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")


def ensure_dir(path):
    """Create a folder under HOME readable by this user only (state holds commands and questions)."""
    HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)


def iterm_session_uuid(env=None):
    """'w0t0p0:243CB74F-...' -> '243CB74F-...'; None outside iTerm2."""
    value = (env if env is not None else os.environ).get("ITERM_SESSION_ID", "")
    uuid = value.split(":", 1)[1] if ":" in value else ""
    # The UUID becomes a file name: accept only a real UUID (no path parts).
    return uuid if UUID_RE.fullmatch(uuid) else None


def _continued_in(transcript, tail_bytes=16384):
    """The session ID that a Claude transcript ends with ("continued-in"), or None."""
    try:
        with open(transcript, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - tail_bytes))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        if '"continued-in"' in line:
            try:
                return json.loads(line).get("continuedInSessionId")
            except (ValueError, AttributeError):
                return None
    return None


def tab_for_session(session_id, transcript_path=None):
    """The iTerm2 tab of an agent session that has no $ITERM_SESSION_ID, or None.

    A Claude Code session sent to the background (a background job) keeps running without the
    tab's environment. The tab is still its home: its state file names this session already,
    or names the session whose transcript ends with "continued-in" this session."""
    if not session_id or not STATE_DIR.is_dir():
        return None
    projects = (Path(transcript_path).parent.parent if transcript_path
                else Path.home() / ".claude" / "projects")
    older = []
    for path in STATE_DIR.glob("*.json"):
        if not UUID_RE.fullmatch(path.stem):
            continue
        try:
            prev = json.loads(path.read_text()).get("agent_session")
        except (OSError, ValueError, AttributeError):
            continue
        if prev == session_id:
            return path.stem
        if isinstance(prev, str) and UUID_RE.fullmatch(prev):  # also keeps glob patterns out
            older.append((path.stem, prev))
    for uuid, prev in older:
        if any(_continued_in(t) == session_id for t in projects.glob(f"*/{prev}.jsonl")):
            return uuid
    return None


def state_path(uuid):
    return STATE_DIR / f"{uuid}.json"


def read_state(uuid):
    try:
        return json.loads(state_path(uuid).read_text())
    except (OSError, ValueError):
        return None


def write_state(uuid, record):
    ensure_dir(STATE_DIR)
    path = state_path(uuid)
    tmp = path.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(record))
    os.replace(tmp, path)


def read_states(max_age_s=2 * 86400):
    """All state files, keyed by iTerm2 session UUID. Drops files older than max_age_s."""
    out = {}
    if not STATE_DIR.is_dir():
        return out
    now = time.time()
    for path in STATE_DIR.glob("*.json"):
        try:
            if now - path.stat().st_mtime > max_age_s:
                path.unlink(missing_ok=True)
                continue
            out[path.stem] = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
    return out


LOG_MAX_BYTES = 5 * 1024 * 1024  # then the log moves to <name>.1 (one old file is kept)


def append_log(name, record):
    ensure_dir(LOG_DIR)
    path = LOG_DIR / name
    try:
        if path.stat().st_size > LOG_MAX_BYTES:
            os.replace(path, path.with_name(name + ".1"))
    except OSError:
        pass
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")
