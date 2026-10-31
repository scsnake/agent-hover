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
# Claude Code StopFailure errors (hooks reference): a usage or account limit needs the person
# (wait for the reset, add credits, switch the model, sign in); other API errors just end the turn.
CLAUDE_LIMIT_ERRORS = {"rate_limit", "billing_error"}
CLAUDE_ACCOUNT_ERRORS = {"authentication_failed", "oauth_org_not_allowed", "account_on_hold", "cloud_credential_error"}
# Claude Code tools that open a dialog for the person.
CLAUDE_DIALOG_TOOLS = {"AskUserQuestion", "ExitPlanMode"}


# Antigravity CLI (agy) hooks (~/.gemini/config/hooks.json): the payload has no event name, so
# the hook command gets it as an argument. PreToolUse and Stop must answer with a decision:
# "allow" hands the tool call on to agy's own permission rules; any Stop decision but
# "continue" lets the turn end.
AGY_EVENTS = ["SessionStart", "PreInvocation", "PreToolUse", "PostToolUse", "PostInvocation", "Stop"]
AGY_REPLY = {"PreToolUse": {"decision": "allow"}, "Stop": {"decision": "stop"}}


def agy_payload(event, raw):
    """An agy hook payload in the Claude-style names that map_event and the hook use."""
    raw = raw if isinstance(raw, dict) else {}
    paths = raw.get("workspacePaths") if isinstance(raw.get("workspacePaths"), list) else []
    call = raw.get("toolCall") if isinstance(raw.get("toolCall"), dict) else {}
    return {"hook_event_name": event, "session_id": raw.get("conversationId"),
            "cwd": paths[0] if paths and isinstance(paths[0], str) else None,
            "tool_name": call.get("name", ""), "transcript_path": raw.get("transcriptPath"),
            "model": raw.get("modelName"), "termination": raw.get("terminationReason", ""),
            "error": raw.get("error") or ""}


def map_event(agent, payload):
    """Return (state, detail) for one hook event, or None to keep the old state."""
    event = payload.get("hook_event_name", "")
    tool = payload.get("tool_name", "")
    if agent == "agy":
        if event == "PreInvocation":
            return WORKING, "thinking"
        if event == "PostInvocation":
            return None
        if event == "Stop":
            reason = payload.get("termination", "")
            return DONE, ("error" if reason == "error" or payload.get("error")
                          else "max steps" if reason == "max_steps_exceeded" else "")
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
        error = payload.get("error", "")
        if error in CLAUDE_LIMIT_ERRORS:
            return WAITING, "usage limit"  # Claude may also continue by itself at the reset
        if error in CLAUDE_ACCOUNT_ERRORS:
            return WAITING, "sign-in or account"
        return DONE, "error"
    if event == "Interrupt":
        # The person pressed Esc, so they are at the tab already.
        return IDLE, "interrupted"
    if event == "Notification" and agent == "claude":
        kind = payload.get("notification_type", "")
        if kind in CLAUDE_WAITING_NOTIFICATIONS:
            return WAITING, kind
        # Usage-limit waits (Claude Code 2.1.234+): it continued, waits for Enter, or gave up.
        if kind == "quota_auto_resume_fired":
            return WORKING, "resumed"
        if kind == "quota_auto_resume_stale":
            return WAITING, "usage limit"
        if kind == "quota_auto_resume_disabled":
            return DONE, "usage limit"
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
    elif event == "StopFailure":  # the API error text, such as "API Error: Rate limit reached"
        lines += [_text(payload.get("last_assistant_message")), _text(payload.get("error_details"))]
    text = "\n".join(line for line in lines if line).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _file_part(path, size, tail):
    try:
        with open(path, "rb") as f:
            if tail:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - size))
            return f.read(size).decode("utf-8", "replace")
    except (OSError, TypeError, ValueError):
        return ""


def codex_rollout(session_id):
    """Codex's session file for a session ID (~/.codex/sessions/YYYY/MM/DD/rollout-...-<id>.jsonl)."""
    if not isinstance(session_id, str) or not UUID_RE.fullmatch(session_id):
        return None
    found = sorted((Path.home() / ".codex" / "sessions").glob(f"*/*/*/rollout-*{session_id}.jsonl"))
    return found[-1] if found else None


CODEX_TURN_TYPES = ("task_started", "task_complete", "turn_aborted")


def codex_last_turn(path, tail_bytes=262144, text=None):
    """The last turn record of the main agent in a Codex session file (task_started,
    task_complete or turn_aborted payload), or None. Codex sends no Stop hook when a turn ends
    with an error (for example usage_limit_exceeded), so the file is the only sign.
    text: the end of the file, already read (a remote host); path is then not used."""
    tail = text if text is not None else _file_part(path, tail_bytes, tail=True)
    for line in reversed(tail.splitlines()):
        if '"event_msg"' not in line or not any(f'"{t}"' in line for t in CODEX_TURN_TYPES):
            continue
        try:
            payload = json.loads(line).get("payload") or {}
        except (ValueError, AttributeError):
            continue  # the cut first line of the tail
        if payload.get("type") not in CODEX_TURN_TYPES:
            continue
        if payload.get("root_turn_id", payload.get("turn_id")) != payload.get("turn_id"):
            continue  # a sub-agent's turn
        return payload
    return None


def codex_turn_state(payload):
    """(state, detail, question) for a Codex turn that ended without a Stop hook, or None."""
    if not payload:
        return None
    if payload.get("type") == "turn_aborted":
        return IDLE, "interrupted", None
    error = payload.get("error") if payload.get("type") == "task_complete" else None
    if not isinstance(error, dict):
        return None
    message = str(error.get("message") or "Codex stopped with an error.")
    if error.get("codex_error_info") == "usage_limit_exceeded":
        # The Codex screen then asks whether to switch to a smaller model.
        return WAITING, "usage limit", "Codex hit its usage limit.\n" + message
    return DONE, "error", None


def codex_resets_at(path, tail_bytes=262144, text=None):
    """When a Codex usage limit resets (Unix time), from the last rate_limits record that has
    limit windows: the latest reset among the windows at 95 % or more (both full: the later one),
    else the fullest window's reset. None if not found. text: as in codex_last_turn."""
    tail = text if text is not None else _file_part(path, tail_bytes, tail=True)
    for line in reversed(tail.splitlines()):
        if '"rate_limits"' not in line:
            continue
        try:
            limits = (json.loads(line).get("payload") or {}).get("rate_limits") or {}
        except (ValueError, AttributeError):
            continue
        windows = [w for w in (limits.get("primary"), limits.get("secondary"))
                   if isinstance(w, dict) and isinstance(w.get("resets_at"), (int, float))]
        if not windows:
            continue  # for example the "premium" entry with no windows
        full = [w for w in windows if (w.get("used_percent") or 0) >= 95]
        pick = max(full, key=lambda w: w["resets_at"]) if full else max(windows, key=lambda w: w.get("used_percent") or 0)
        return float(pick["resets_at"])
    return None


def codex_seed_state(payload):
    """(state, detail, question) of a Codex tab from its last turn record (for a tab with no
    hook event yet): a started turn is working, a normal end is done, see codex_turn_state."""
    if not payload:
        return None
    if payload.get("type") == "task_started":
        return WORKING, "", None
    if payload.get("type") == "task_complete" and not payload.get("error"):
        return DONE, "", None
    return codex_turn_state(payload)


SDK_CLI_RE = re.compile(r'"entrypoint":\s*"sdk-cli"')  # Claude transcript line of a `claude -p` run
CODEX_EXEC_RE = re.compile(r'"source":\s*"exec"|"originator":\s*"codex_exec"')  # Codex session_meta


def nested_run(agent, payload, env=None):
    """True for an agent run as a command, not by the person: `claude -p` or `codex exec`, often
    started by the agent in the same tab (it inherits the tab's ITERM_SESSION_ID). Its events
    must not change that tab's state (a `claude -p` SessionEnd hid a running Codex tab)."""
    env = os.environ if env is None else env
    transcript = payload.get("transcript_path")
    if agent == "claude":
        if env.get("CLAUDE_CODE_ENTRYPOINT") == "sdk-cli":
            return True
        # Claude writes "entrypoint" on each line; one huge last line can hide it from the tail.
        return any(SDK_CLI_RE.search(_file_part(transcript, 8192, tail=t)) for t in (True, False))
    if agent == "codex":
        head = _file_part(transcript or codex_rollout(payload.get("session_id")), 8192, tail=False)
        return bool(CODEX_EXEC_RE.search(head))
    if agent == "agy":
        # `agy -p` (print mode), for example a review run by another agent in its tab.
        return bool(env.get("AGY_REVIEW_ACTIVE")) or agy_print_mode()
    return False


AGY_PRINT_FLAGS = {"-p", "--print", "--prompt"}


def agy_print_mode(pid=None):
    """True if the nearest agy process above this hook runs in print mode (-p)."""
    import subprocess
    pid = os.getppid() if pid is None else pid
    for _ in range(6):
        try:
            out = subprocess.run(["ps", "-o", "ppid=,args=", "-p", str(pid)], capture_output=True,
                                 text=True, timeout=2).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return False
        if not out:
            return False
        ppid, _, args = out.partition(" ")
        words = args.split()
        if words and words[0].rsplit("/", 1)[-1] == "agy":
            return any(w in AGY_PRINT_FLAGS or w.startswith(("--print=", "--prompt=")) for w in words[1:])
        try:
            pid = int(ppid)
        except ValueError:
            return False
        if pid <= 1:
            return False
    return False


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
                continue  # a cut first line, or the words inside a message: look further up
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


def codex_tab(session_id, cwd, max_age_s=120):
    """The open Codex tab (a row of panel.json) of a Codex session, or None: the tab that names
    the session, else the one in this folder (a tab with no session yet first; two such tabs:
    None, no guess). For hooks that run without the tab's environment."""
    try:
        panel = json.loads((HOME / "panel.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(panel, dict) or time.time() - panel.get("ts", 0) > max_age_s:
        return None
    tabs = [r for r in panel.get("rows", []) if isinstance(r, dict) and r.get("agent") == "codex"
            and not r.get("host") and UUID_RE.fullmatch(str(r.get("session")))]
    known = {r["session"]: (read_state(r["session"]) or {}).get("agent_session") for r in tabs}
    named = [t for t, sid in known.items() if session_id and sid == session_id]
    if named:
        return named[0]
    if not cwd:
        return None
    where = os.path.realpath(cwd)
    here = [r["session"] for r in tabs if r.get("cwd") and os.path.realpath(r["cwd"]) == where]
    new = [t for t in here if not known[t]]
    if len(new) == 1:
        return new[0]
    return here[0] if len(here) == 1 else None


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


def read_states():
    """All state files, keyed by iTerm2 session UUID."""
    out = {}
    if not STATE_DIR.is_dir():
        return out
    for path in STATE_DIR.glob("*.json"):
        try:
            out[path.stem] = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
    return out


def prune_states(live, max_age_s=2 * 86400, now=None):
    """Delete the state files of closed tabs (not in live) unchanged for max_age_s. A live tab
    keeps its file however old: an agent can be idle for days. Returns the deleted sessions."""
    now = time.time() if now is None else now
    gone = []
    for path in STATE_DIR.glob("*.json") if STATE_DIR.is_dir() else []:
        try:
            if path.stem not in live and now - path.stat().st_mtime > max_age_s:
                path.unlink(missing_ok=True)
                gone.append(path.stem)
        except OSError:
            continue
    return gone


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
