"""Agent helpers with no iTerm2 part: find the agent in a process tree, read Claude's and Codex's
own session files, and measure CPU and memory. The Mac daemon and the remote tmux watcher
(tmux_watch.py) both use them.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

from agent_hover import states


def clean_name(name):
    """'✳ Docs site' -> 'Docs site': drop the status glyph or spinner that agents put first."""
    return re.sub(r"^[^\w(\[]+", "", name or "").strip()


AGENTS = ["claude", "codex", "agy"]  # plus config.json "agents": {"extra": [...]}
# Claude Code keeps one file per running process: {"pid", "sessionId", "cwd", "status", ...}.
CLAUDE_SESSIONS = Path.home() / ".claude" / "sessions"
CLAUDE_STATUS = {"busy": states.WORKING, "idle": states.IDLE, "waiting": states.WAITING}


def detect_agent(process_title, command_line):
    """'claude' or 'codex' if the tab's foreground job is that agent, else None.

    iTerm2's jobName is Claude's version (for example 2.1.305), so use processTitle and the
    first two words of commandLine (the second covers 'node .../codex')."""
    names = [process_title or ""] + [w.rsplit("/", 1)[-1] for w in (command_line or "").split()[:2]]
    return next((a for a in AGENTS if a in names), None)


def title_state(auto_name):
    """Claude's title glyph: '✳' = idle, another leading glyph (a spinner) = working."""
    m = re.match(r"^[^\w(\[]+", auto_name or "")
    glyph = m.group(0).strip() if m else ""
    if not glyph or glyph.startswith("✳"):
        return states.IDLE
    return states.WORKING


def seed_record(sid, agent, auto_name, path, now):
    """State for an agent tab that has sent no hook event since the install."""
    return {"iterm": sid, "agent": agent, "state": title_state(auto_name), "detail": "seen by daemon",
            "event": "seed", "ts": now, "agent_session": None, "cwd": path}


def claude_session(pid):
    """Claude Code's own file for a running process, or None."""
    try:
        pid = int(pid)
        data = json.loads((CLAUDE_SESSIONS / f"{pid}.json").read_text())
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) and data.get("pid") == pid else None


def running_claude():
    """{session ID: ("claude", pid)} of the running Claude processes (their own files)."""
    out = {}
    for path in CLAUDE_SESSIONS.glob("*.json"):
        info = claude_session(path.stem) if path.stem.isdigit() else None
        if info and isinstance(info.get("sessionId"), str):
            out[info["sessionId"]] = ("claude", info["pid"])
    return out


CODEX_PIDS = {}  # Codex process ID -> its main session file, found once with lsof


def open_files(pid):
    """Paths of the files a process keeps open: /proc on Linux, lsof on macOS."""
    fd_dir = Path(f"/proc/{int(pid)}/fd")
    if fd_dir.is_dir():
        paths = []
        try:
            for fd in fd_dir.iterdir():
                try:
                    paths.append(os.readlink(fd))
                except OSError:
                    continue
        except OSError:
            pass
        return paths
    try:
        out = subprocess.run(["lsof", "-p", str(int(pid)), "-Fn"], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, ValueError, subprocess.SubprocessError):
        out = ""
    return [line[1:] for line in out.splitlines() if line.startswith("n")]


def codex_main_rollout(pid):
    """The session file of a running Codex process's main thread (it keeps its session files
    open; sub-agent threads have their own, marked "thread_source":"subagent"), or None."""
    if pid in CODEX_PIDS:
        return CODEX_PIDS[pid]
    found = None
    try:
        paths = open_files(pid)
    except (TypeError, ValueError):
        paths = []
    for path in paths:
        if "/.codex/sessions/" in path and Path(path).name.startswith("rollout-"):
            head = states._file_part(path, 8192, tail=False)
            if re.search(r'"thread_source":\s*"user"', head) and not states.CODEX_EXEC_RE.search(head):
                found = path
                break
    if found:  # none yet (a new Codex may not have written its file): look again next scan
        CODEX_PIDS[pid] = found
    return found




def with_codex_session(rec, path):
    """A seeded Codex record with the session ID, folder and state from its session file."""
    if not path:
        return rec
    new = dict(rec)
    sid = Path(path).stem[-36:]
    if states.UUID_RE.fullmatch(sid):
        new["agent_session"] = sid
    m = re.search(r'"cwd":\s*"((?:[^"\\]|\\.)*)"', states._file_part(path, 8192, tail=False))
    if m:
        new["cwd"] = json.loads(f'"{m.group(1)}"')
    turn = states.codex_last_turn(path)
    found = states.codex_seed_state(turn)
    if found and found[0] != rec.get("state"):
        state, detail, question = found
        when = turn.get("completed_at") or turn.get("started_at")
        new.update(state=state, detail=detail, ts=float(when) if isinstance(when, (int, float)) else time.time())
        if question:
            new["question"] = question
    if new.get("detail") == "usage limit" and new.get("state") == states.WAITING and not new.get("resets_at"):
        new["resets_at"] = states.codex_resets_at(path)
    if new.get("detail") == "seen by daemon":
        new["detail"] = ""
    return new


def with_claude_session(rec, info, now):
    """A seeded record with the session ID, folder and state from Claude's own file (a tab with
    no hook event yet: Claude started before the install, or its hooks do not reach us)."""
    if not info:
        return rec
    new = dict(rec)
    sid = info.get("sessionId")
    if isinstance(sid, str) and states.UUID_RE.fullmatch(sid):
        new["agent_session"] = sid
    if isinstance(info.get("cwd"), str) and info["cwd"]:
        new["cwd"] = info["cwd"]
    state = CLAUDE_STATUS.get(info.get("status"))
    if state and state != rec.get("state"):
        new["state"] = state
        changed = info.get("statusUpdatedAt")
        new["ts"] = changed / 1000 if isinstance(changed, (int, float)) else now
    if new.get("detail") in ("seen by daemon", "Claude session file"):
        new["detail"] = ""  # internal notes, not for the panel (it shows a waiting row's detail)
    return new


def process_table():
    """pid -> (parent pid, % CPU, resident memory in KB) for all processes (one `ps` call)."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,pcpu=,rss="], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    table = {}
    for line in out.splitlines():
        parts = line.split()
        try:
            table[int(parts[0])] = (int(parts[1]), float(parts[2]), int(parts[3]))
        except (IndexError, ValueError):
            continue
    return table


def as_pid(value):
    """iTerm2's jobPid as a positive int, or None."""
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def child_map(table):
    """parent pid -> child pids, built once per scan."""
    children = {}
    for pid, (ppid, _, _) in table.items():
        children.setdefault(ppid, []).append(pid)
    return children


def tree_usage(table, root, children=None):
    """(% CPU, memory in MB) of a process and all its children: the agent, its MCP servers and
    the commands it runs. macOS %CPU is a decaying recent average."""
    children = child_map(table) if children is None else children
    cpu = kb = 0.0
    todo, seen = [root], set()
    while todo:
        pid = todo.pop()
        if pid in seen or pid not in table:
            continue
        seen.add(pid)
        cpu += table[pid][1]
        kb += table[pid][2]
        todo.extend(children.get(pid, []))
    return round(cpu, 1), round(kb / 1024)



AGY_PIDS = {}  # agy process ID -> its conversation ID, found once with lsof
AGY_LOCK_RE = re.compile(r"/\.gemini/antigravity-cli/presence/([0-9a-fA-F-]{36})\.lock$")


def agy_conversation(pid):
    """The conversation of a running agy CLI: it keeps presence/<conversation ID>.lock open."""
    if pid in AGY_PIDS:
        return AGY_PIDS[pid]
    try:
        found = next((m.group(1) for m in map(AGY_LOCK_RE.search, open_files(pid)) if m), None)
    except (TypeError, ValueError):
        found = None
    if found:
        AGY_PIDS[pid] = found
    return found


AGY_SUMMARIES = Path.home() / ".gemini" / "antigravity-cli" / "conversation_summaries.db"


def agy_titles(conversations):
    """conversation ID -> title (agy's /rename, or its own summary title) from agy's summary
    database, read-only. agy sets a fixed terminal title, so this is the row name. {} on error."""
    ids = [c for c in conversations if c and states.UUID_RE.fullmatch(c)]
    if not ids or not AGY_SUMMARIES.exists():
        return {}
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{AGY_SUMMARIES}?mode=ro", uri=True, timeout=0.5)
        try:
            rows = con.execute("select conversation_id, title from conversation_summaries where conversation_id in "
                               f"({','.join('?' * len(ids))})", ids).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    return {cid: title.strip() for cid, title in rows if isinstance(title, str) and title.strip()}


def agy_tree():
    """conversation ID -> (parent ID or "", status) for all agy conversations, read-only. A
    sub-agent has its own conversation, with the main one as parent. {} on error."""
    if not AGY_SUMMARIES.exists():
        return {}
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{AGY_SUMMARIES}?mode=ro", uri=True, timeout=0.5)
        try:
            rows = con.execute("select conversation_id, parent_conversation_id, status from conversation_summaries").fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    return {cid: (parent or "", status or "") for cid, parent, status in rows if cid}


def agy_root(conversation, tree=None):
    """(main conversation ID, is_sub_agent) for an agy conversation, through its parents.
    (conversation, False) if agy has no row for it yet (unknown)."""
    tree = agy_tree() if tree is None else tree
    cid, seen = conversation, set()
    while cid in tree and tree[cid][0] and cid not in seen and len(seen) < 16:
        seen.add(cid)
        cid = tree[cid][0]
    return cid, cid != conversation


def agy_running_family(root, tree=None):
    """The main conversation and its sub-agents that run now (they can ask for permission)."""
    tree = agy_tree() if tree is None else tree
    out = [root]
    for cid, (_, status) in tree.items():
        if cid != root and status in ("CASCADE_RUN_STATUS_RUNNING", "CASCADE_RUN_STATUS_BUSY") \
                and agy_root(cid, tree)[0] == root:
            out.append(cid)
    return out


def agy_idle(root, tree=None):
    """True when agy lists the main conversation as idle and none of its sub-agents runs."""
    tree = agy_tree() if tree is None else tree
    return tree.get(root, ("", ""))[1] == "CASCADE_RUN_STATUS_IDLE" and agy_running_family(root, tree) == [root]


AGY_BRAIN = Path.home() / ".gemini" / "antigravity-cli" / "brain"


def agy_first_prompt(conversation, limit=40):
    """The first line of the first prompt in an agy conversation, cut to limit characters: the
    row name until agy has made its own title. None if not found."""
    if not conversation or not states.UUID_RE.fullmatch(conversation):
        return None
    path = AGY_BRAIN / conversation / ".system_generated" / "logs" / "transcript.jsonl"
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for _, line in zip(range(50), f):
                try:
                    step = json.loads(line)
                except ValueError:
                    continue
                if step.get("type") == "USER_INPUT" and isinstance(step.get("content"), str):
                    text = re.sub(r"</?USER_REQUEST>", "", step["content"])
                    first = next((x.strip() for x in text.splitlines() if x.strip()), "")
                    return (first[: limit - 1] + "…" if len(first) > limit else first) or None
    except OSError:
        return None
    return None


AGY_CONVERSATIONS = Path.home() / ".gemini" / "antigravity-cli" / "conversations"
AGY_STEP_WAITING = 9  # CORTEX_STEP_STATUS_WAITING (enum in agy's protobuf descriptor): asks the person


def agy_waiting_step(conversation):
    """(tool, args) of the step an agy conversation waits on ("Run this command?"), or None.
    agy sends no hook event for its permission prompt; its conversation database marks the step
    WAITING, and the step's data holds the tool call as JSON. Read-only."""
    if not conversation or not states.UUID_RE.fullmatch(conversation):
        return None
    path = AGY_CONVERSATIONS / f"{conversation}.db"
    if not path.exists():
        return None
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.5)
        try:
            row = con.execute("select metadata from steps where status = ? order by idx desc limit 1",
                              (AGY_STEP_WAITING,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    text = bytes(row[0] or b"").decode("utf-8", "replace")
    start = text.find('{"')
    args = {}
    if start >= 0:
        try:
            found, _ = json.JSONDecoder().raw_decode(text[start:])
            args = found if isinstance(found, dict) else {}
        except ValueError:
            args = {}
    names = re.findall(r"[a-z][a-z_]{2,40}", text[:start] if start >= 0 else text[:200])
    return (names[-1] if names else "tool"), args


def agy_question(tool, args):
    """The panel's question text for an agy permission prompt."""
    if tool == "run_command" and isinstance(args.get("CommandLine"), str):
        where = f"\n(in {args['Cwd']})" if isinstance(args.get("Cwd"), str) else ""
        return f"Run this command?\n{args['CommandLine']}{where}"
    return f"Allow {tool}?" + (f"\n{json.dumps(args, ensure_ascii=False)}" if args else "")


def with_agy_session(rec, conversation):
    """A seeded agy record with its conversation ID (agy has no status file: the state stays
    idle until its first hook event)."""
    new = dict(rec)
    if conversation and states.UUID_RE.fullmatch(conversation):
        new["agent_session"] = conversation
    if new.get("detail") == "seen by daemon":
        new["detail"] = ""
    return new


def codex_turn_update(rec, path, now):
    """A working Codex record, updated from the end of its session file: Codex sends no Stop hook
    when a turn ends with an error (a usage limit, for example). None if the turn still runs."""
    turn = states.codex_last_turn(path)
    end = states.codex_turn_state(turn)
    if not end:
        return None
    state, detail, question = end
    ended = turn.get("completed_at")
    new = {**rec, "state": state, "detail": detail, "event": "codex_turn_end",
           "ts": float(ended) if isinstance(ended, (int, float)) and ended <= now else now}
    if question:
        new["question"] = question
    if detail == "usage limit":
        new["resets_at"] = states.codex_resets_at(path)
    return new
