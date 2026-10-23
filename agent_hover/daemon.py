"""agent-hover daemon: keep N active agent tabs in iTerm2; bury and unbury by agent state.

  uv run python -m agent_hover.daemon [--apply] [--n 5] [--idle-min 20] [--working-park-s 120]
  uv run python -m agent_hover.daemon --restore     # unbury every session this tool buried, then exit

Without --apply it only logs what it would do (dry run).
On SIGTERM / SIGINT it unburies every session it buried.
"""
import argparse
import asyncio
import ctypes
import fcntl
import json
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import iterm2

from agent_hover import search, states
from agent_hover.rules import Config, Tab, decide

TICK_S = 1.0
NAME_REFRESH_S = 5.0
MODEL_REFRESH_S = 30.0  # read each tab's model from its transcript this often
USAGE_SCAN_S = 5.0  # CPU and memory of each agent's process tree, this often
UPDATE_FILE = states.HOME / "update.json"
UPDATE_EVERY_S = 86400.0  # the opt-in update check ("update_check": true in config.json), once a day
REPO = Path(__file__).resolve().parent.parent
SLOW_TICK_S = 0.5
TYPING_QUIET_S = 3.0  # automatic unbury waits until no key was pressed for this long  # log a "slow_tick" line when one tick (without the wait) takes longer
REQUEST_DIR = states.HOME / "requests"
PANEL_FILE = states.HOME / "panel.json"
PEEK_FILE = states.HOME / "peek.json"  # last screen lines of one tab, for the panel's hover preview
PINS_FILE = states.HOME / "pins.json"  # tabs pinned in the panel: never parked
GROUP_ORDER = {states.WAITING: 0, states.DONE: 1, states.WORKING: 2, states.IDLE: 3}


def clean_name(name):
    """'✳ Docs site' -> 'Docs site': drop the status glyph or spinner that agents put first."""
    return re.sub(r"^[^\w(\[]+", "", name or "").strip()


AGENTS = ["claude", "codex"]  # plus config.json "agents": {"extra": [...]}
SEED_SCAN_S = 5.0
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


async def seed_agents(app, known, checked):
    """Add agent tabs (job claude/codex) that have no state file yet, and keep seeded Claude tabs
    up to date from Claude's own session file (session ID, folder, busy / idle / waiting).

    known: the state records. checked: session -> jobPid already looked at, so a tab with no
    record is read again only when its job changes."""
    now = time.time()
    for w in windows(app):
        for t in w.tabs:
            for s in t.all_sessions:
                sid = s.session_id
                rec = known.get(sid)
                if rec is not None and rec.get("event") != "seed":
                    continue  # the hooks report this tab
                pid = await s.async_get_variable("jobPid")
                if rec is not None:
                    if rec.get("agent") == "claude":
                        new = with_claude_session(rec, claude_session(pid), now)
                        latest = states.read_state(sid) or {}
                        if new != rec and latest.get("event") == "seed":  # a hook may have just written
                            states.write_state(sid, new)
                            if new.get("agent_session") != rec.get("agent_session"):
                                log("seed_session", session=sid, agent_session=new.get("agent_session"))
                    continue
                if checked.get(sid) == pid:
                    continue
                checked[sid] = pid
                agent = detect_agent(await s.async_get_variable("processTitle"),
                                     await s.async_get_variable("commandLine"))
                if agent and not states.state_path(sid).exists():  # a hook may have just written it
                    rec = seed_record(sid, agent, await s.async_get_variable("autoName"),
                                      await s.async_get_variable("path"), now)
                    if agent == "claude":
                        rec = with_claude_session(rec, claude_session(pid), now)
                    states.write_state(sid, rec)
                    log("seed", session=sid, agent=agent, state=rec["state"],
                        agent_session=rec.get("agent_session"))


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


def tree_usage(table, root):
    """(% CPU, memory in MB) of a process and all its children: the agent, its MCP servers and
    the commands it runs. macOS %CPU is a decaying recent average."""
    children = {}
    for pid, (ppid, _, _) in table.items():
        children.setdefault(ppid, []).append(pid)
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


def hot_update(hot_since, sid, state, cpu, now, cfg_pct, cfg_min):
    """True when an agent that is not working has used cfg_pct % CPU or more for cfg_min minutes
    (a stuck agent, or a command it left running)."""
    if cfg_pct > 0 and cpu >= cfg_pct and state != states.WORKING:
        start = hot_since.setdefault(sid, now)
        return now - start >= cfg_min * 60
    hot_since.pop(sid, None)
    return False


def check_update(repo=REPO):
    """Compare this checkout's commit with the HEAD of its "origin" remote (git ls-remote: about
    1 s, no download). Only tells; never pulls (./run.sh update does that, when the person runs it).
    None when there is no remote or no network."""
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=20)
    try:
        if git("remote", "get-url", "origin").returncode != 0:
            return None
        local = git("rev-parse", "HEAD").stdout.strip()
        found = git("ls-remote", "origin", "HEAD").stdout.split()
        remote = found[0] if found else ""
        if not local or not remote:
            return None
        # Behind: the remote commit differs and is not already in the local history.
        behind = remote != local and git("merge-base", "--is-ancestor", remote, "HEAD").returncode != 0
    except (OSError, subprocess.SubprocessError):
        return None
    return {"checked": time.time(), "local": local, "remote": remote, "available": behind}


def load_update():
    try:
        data = json.loads(UPDATE_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_panel(rows, apply, update=False):
    """Snapshot for the hover panel: visible agent tabs by group, buried ones last."""
    rows.sort(key=lambda r: (r["buried"], GROUP_ORDER.get(r["state"], 9), -r["since"]))
    states.HOME.mkdir(parents=True, exist_ok=True)
    tmp = PANEL_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"ts": time.time(), "apply": apply, "update": update, "rows": rows}))
    tmp.replace(PANEL_FILE)


def pending_requests():
    try:
        return sorted(REQUEST_DIR.glob("*.json"))
    except OSError:
        return []


def log(kind, **kw):
    rec = {"ts": round(time.time(), 3), "kind": kind, **kw}
    states.append_log("daemon.jsonl", rec)
    print(json.dumps(rec), flush=True)


def load_buried():
    try:
        return json.loads(states.BURIED_FILE.read_text())
    except (OSError, ValueError):
        return {}


def load_pins():
    try:
        data = json.loads(PINS_FILE.read_text())
    except (OSError, ValueError):
        return set()
    return {s for s in data if isinstance(s, str)} if isinstance(data, list) else set()


def save_pins(pins):
    states.HOME.mkdir(parents=True, exist_ok=True)
    tmp = PINS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(pins)))
    tmp.replace(PINS_FILE)


def save_buried(data):
    states.HOME.mkdir(parents=True, exist_ok=True)
    tmp = states.BURIED_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(states.BURIED_FILE)


class Focus:
    """Polled from iTerm2 on each tick and before each bury; no event stream to miss."""

    def __init__(self):
        self.session = None
        self.app_active = False
        self.changed_ts = 0.0

    async def refresh(self, app):
        await app.async_refresh_focus()
        win = app.current_terminal_window
        tab = win.current_tab if win else None
        sid = tab.current_session.session_id if tab and tab.current_session else None
        active = bool(app.app_active)
        if (sid, active) != (self.session, self.app_active):
            self.session, self.app_active, self.changed_ts = sid, active, time.time()

    def has(self, sid):
        return self.app_active and sid == self.session


ONLY_WINDOWS = set()  # --only-window: manage only these windows (tests); empty = all


def windows(app):
    return [w for w in app.terminal_windows if not ONLY_WINDOWS or w.window_id in ONLY_WINDOWS]


def layout(app):
    """session -> (window, tab, sessions in tab, index), and visible tab count per window.

    Uses all_sessions, so a tab with a maximized or floating pane counts as split."""
    where, counts = {}, {}
    for w in windows(app):
        counts[w.window_id] = len(w.tabs)
        for i, t in enumerate(w.tabs):
            for s in t.all_sessions:
                where[s.session_id] = (w, t, len(t.all_sessions), i)
    return where, counts


try:
    _CG = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    _CG.CGEventSourceSecondsSinceLastEventType.restype = ctypes.c_double
    _CG.CGEventSourceSecondsSinceLastEventType.argtypes = [ctypes.c_int32, ctypes.c_uint32]
except (OSError, AttributeError):
    _CG = None


def keyboard_idle_s():
    """Seconds since the last key press in any app (HID state; needs no permission).

    An unbury opens the tab as a new selected tab in the front window for a moment, so keys
    typed then would go to the wrong agent: unbury only when the keyboard is quiet."""
    if _CG is None:
        return 1e9
    since = _CG.CGEventSourceSecondsSinceLastEventType
    return min(since(1, 10), since(1, 12))  # HID system state; kCGEventKeyDown, kCGEventFlagsChanged


async def wait_keyboard_quiet(max_wait_s):
    """Wait until no key was pressed for TYPING_QUIET_S, or max_wait_s passed."""
    end = time.time() + max_wait_s
    while keyboard_idle_s() < TYPING_QUIET_S and time.time() < end:
        await asyncio.sleep(0.25)


def find_tab(app, sid):
    for w in app.terminal_windows:
        for t in w.tabs:
            if any(s.session_id == sid for s in t.all_sessions):
                return w, t
    return None, None


async def unbury(app, sid, info, keep_selection=True):
    """Unbury, then move the tab back to its old window and place.

    iTerm2 puts an unburied session in a new tab of the front window. Afterwards, every
    window gets back the tab it had selected, and the window that had the keyboard gets it
    back (the move to another window can take it)."""
    session = next((s for s in app.buried_sessions if s.session_id == sid), None)
    if session is None:
        return False
    selected = {w.window_id: w.current_tab.tab_id for w in app.terminal_windows if w.current_tab}
    await app.async_refresh_focus()
    key_window = app.current_terminal_window.window_id if app.current_terminal_window else None
    was_active = app.app_active
    await session.async_set_buried(False)
    for _ in range(20):
        await asyncio.sleep(0.05)
        await app.async_refresh()
        win, tab = find_tab(app, sid)
        if tab:
            break
    else:
        return True
    home = app.get_window_by_id(info.get("window", "")) or win
    tabs = [t for t in home.tabs if t.tab_id != tab.tab_id]
    tabs.insert(min(info.get("index", len(tabs)), len(tabs)), tab)
    if [t.tab_id for t in tabs] != [t.tab_id for t in home.tabs]:
        await home.async_set_tabs(tabs)
        await app.async_refresh()
    for w in app.terminal_windows if keep_selection else []:
        old = app.get_tab_by_id(selected.get(w.window_id, ""))
        if (old and w.current_tab and w.current_tab.tab_id != old.tab_id
                and old.tab_id in [t.tab_id for t in w.tabs]):
            await old.async_activate(order_window_front=False)
    if keep_selection and was_active and key_window:
        await app.async_refresh_focus()
        now_key = app.current_terminal_window
        if now_key is None or now_key.window_id != key_window:
            back = app.get_window_by_id(key_window)
            if back:
                await back.async_activate()
    return True


async def bury(app, sid, focus, ours, reason):
    """Bury one session after a fresh check of layout and focus. Return True if buried."""
    await app.async_refresh()
    await focus.refresh(app)
    where, counts = layout(app)
    if sid not in where:
        return False
    win, tab, n_sessions, index = where[sid]
    selected = win.current_tab is not None and win.current_tab.tab_id == tab.tab_id
    why_not = ("focused" if focus.has(sid) else "selected tab of its window" if selected
               else "split tab" if n_sessions > 1
               else "last tab of its window" if counts.get(win.window_id, 0) <= 1 else None)
    if why_not:
        log("skip_bury", session=sid, why=why_not)
        return False
    session = app.get_session_by_id(sid)
    if session is None:
        return False
    ours[sid] = {"window": win.window_id, "index": index, "ts": time.time(), "reason": reason}
    save_buried(ours)  # record first, so --restore works even after a crash
    await session.async_set_buried(True)
    return True


PEEK_LINES = 14


async def write_peek(app, sid):
    """The last screen lines of a tab (shown or buried), for the panel's hover preview."""
    lines = []
    session = app.get_session_by_id(sid)
    if session is not None:
        try:
            screen = await session.async_get_screen_contents()
            lines = [screen.line(i).string.rstrip() for i in range(screen.number_of_lines)]
        except Exception:  # noqa: BLE001 - a closed or odd session: show nothing
            lines = []
    while lines and not lines[-1]:
        lines.pop()
    states.HOME.mkdir(parents=True, exist_ok=True)
    tmp = PEEK_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"session": sid, "ts": time.time(), "lines": lines[-PEEK_LINES:]}))
    tmp.replace(PEEK_FILE)


async def handle_requests(app, ours, last_focus):
    """Clicks from the hover panel: raise the tab (unbury if needed), select it and bring
    iTerm2 to the front. The click counts as a focus, not a pin: the normal rules apply
    from then on, so an idle tab is buried again after idle_park_s without focus.
    Works in dry run too: it is the person's own action.
    "pin" / "unpin" (panel right-click): a pinned tab is never parked; the rules raise it
    if it is buried.
    "resume" (a closed agent in the panel's search): see open_resume_tab."""
    buried_now = {x.session_id for x in app.buried_sessions}
    for f in pending_requests():
        try:
            req = json.loads(f.read_text())
        except (OSError, ValueError):
            req = {}
        f.unlink(missing_ok=True)
        sid = req.get("session", "")
        if req.get("action") == "peek" and sid:
            await write_peek(app, sid)
            continue
        if req.get("action") in ("pin", "unpin") and sid:
            pins = load_pins()
            (pins.add if req["action"] == "pin" else pins.discard)(sid)
            save_pins(pins)
            log(req["action"], session=sid)
            continue
        if req.get("action") == "resume":
            await open_resume_tab(app, req)
            continue
        if req.get("action") != "raise" or not sid:
            continue
        was_buried = sid in buried_now
        if was_buried:
            await unbury(app, sid, ours.pop(sid, {}), keep_selection=False)
            save_buried(ours)
        await app.async_refresh()
        session = app.get_session_by_id(sid)
        if session is None:
            log("raise_request", session=sid, found=False)
            continue
        await session.async_activate(select_tab=True, order_window_front=True)
        await app.async_activate(raise_all_windows=False, ignoring_other_apps=True)
        last_focus[sid] = time.time()
        log("raise_request", session=sid, found=True, was_buried=was_buried,
            lag_s=round(time.time() - req.get("ts", time.time()), 2))


async def open_resume_tab(app, req):
    """A new tab in the front window: run `cd <folder>`, then type the agent's resume command
    without Enter, so the person checks it and starts it. The request names only the agent, the
    session ID and the folder; the command is built here, so a request file cannot type other text."""
    agent, sid, cwd = req.get("agent"), str(req.get("agent_session", "")), str(req.get("cwd", ""))
    if agent not in ("claude", "codex") or not states.UUID_RE.fullmatch(sid) or not Path(cwd).is_dir():
        log("resume_request", ok=False, agent=agent, agent_session=sid, cwd=cwd)
        return
    window = app.current_terminal_window
    if window is not None:
        session = (await window.async_create_tab()).current_session
    else:
        window = await iterm2.Window.async_create(app.connection)
        session = window.current_session
    await asyncio.sleep(0.5)  # let the new shell start, so it reads the typed text at its prompt
    await session.async_send_text(f"cd {shlex.quote(cwd)}\n")
    await session.async_send_text(search.resume_command(agent, sid, ""))
    await session.async_activate(select_tab=True, order_window_front=True)
    await app.async_activate(raise_all_windows=False, ignoring_other_apps=True)
    log("resume_request", ok=True, agent=agent, agent_session=sid, new_session=session.session_id)


async def restore_all(app):
    """Newest bury first: each saved index is exact for the window as it was at that bury."""
    buried = load_buried()
    for sid, info in sorted(buried.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True):
        await wait_keyboard_quiet(5)  # run.sh stop waits up to 120 s
        ok = await unbury(app, sid, info)
        log("restore", session=sid, found=ok)
        buried.pop(sid)
        save_buried(buried)


async def run(conn, args):
    app = await iterm2.async_get_app(conn)
    if args.restore:
        await restore_all(app)
        return
    cfg = Config(n_active=args.n, idle_park_s=args.idle_min * 60, working_park_s=args.working_park_s)
    focus = Focus()
    await focus.refresh(app)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    started = time.time()
    last_focus, pinned, last_sig = {}, {}, None
    deferring = False  # automatic unburies wait while the person types
    since = {}  # session -> (state, time the daemon first saw that state)
    names, names_ts = {}, 0.0
    models = {}  # session -> (time read, model or None)
    usage, hot, hot_since, usage_ts = {}, {}, {}, 0.0  # session -> (cpu, MB) / bool / time hot began
    update_on = states.load_config().get("update_check") is True
    update = load_update() if update_on else {}
    update_ts = update.get("checked", 0.0) if isinstance(update.get("checked"), (int, float)) else 0.0
    seed_checked, seed_ts = {}, 0.0
    log("start", apply=args.apply, n=cfg.n_active, idle_park_s=cfg.idle_park_s,
        working_park_s=cfg.working_park_s)
    while not stop.is_set():
        now = time.time()
        t0 = time.monotonic()
        parts = {}  # seconds per part of this tick, logged only when the tick is slow
        await app.async_refresh()
        await focus.refresh(app)
        parts["refresh"] = time.monotonic() - t0
        if now - seed_ts > SEED_SCAN_S:
            t = time.monotonic()
            await seed_agents(app, states.read_states(), seed_checked)
            parts["seed"] = time.monotonic() - t
            seed_ts = now
        recs = states.read_states()
        where, counts = layout(app)
        buried_now = {s.session_id for s in app.buried_sessions}
        ours = load_buried()

        # The person unburied one of ours by hand: pin it until its state changes.
        for sid in list(ours):
            if sid not in buried_now:
                ours.pop(sid)
                if sid in recs:
                    pinned[sid] = recs[sid].get("state")
                log("manual_unbury", session=sid)
        save_buried(ours)
        for sid in list(pinned):
            if recs.get(sid, {}).get("state") != pinned[sid]:
                pinned.pop(sid)
        pins = load_pins()
        if any(sid not in where and sid not in buried_now for sid in pins):  # tab closed
            pins = {sid for sid in pins if sid in where or sid in buried_now}
            save_pins(pins)

        if pending_requests():
            t = time.monotonic()
            await handle_requests(app, ours, last_focus)
            parts["requests"] = time.monotonic() - t
            await app.async_refresh()
            await focus.refresh(app)
            where, counts = layout(app)
            buried_now = {x.session_id for x in app.buried_sessions}
            pins = load_pins()  # a pin or unpin request changes it

        if focus.session and focus.app_active:
            last_focus[focus.session] = now
        tabs, release = [], []
        for sid in ours:
            if sid not in recs or recs[sid].get("state") == states.ENDED:
                release.append(sid)  # agent gone or state unknown: give the tab back
        for sid, r in recs.items():
            if r.get("state") == states.ENDED or sid in release:
                continue
            selected = False
            if sid in where:
                win, tab, n_sessions, _ = where[sid]
                if n_sessions > 1:
                    continue  # split panes: not managed in POC 1
                window, is_buried = win.window_id, False
                selected = win.current_tab is not None and win.current_tab.tab_id == tab.tab_id
            elif sid in ours:
                window, is_buried = ours[sid]["window"], True
            else:
                continue  # closed, or buried by the person
            # At start, treat old visible states as seen, so old "done" tabs can park later.
            # A tab that was buried (for example during a crash) is not seen: it must come back.
            if since.get(sid, (None,))[0] != r["state"]:
                since[sid] = (r["state"], r["ts"])
            old_visible = r["ts"] < started - 60 and sid not in ours
            lf = last_focus.setdefault(sid, started if old_visible else 0.0)
            tabs.append(Tab(session=sid, window=window, state=r["state"], state_ts=r["ts"],
                            buried=is_buried, focused=focus.has(sid), last_focus_ts=lf,
                            pinned=pinned.get(sid) == r["state"] or sid in pins, state_since=since[sid][1],
                            selected=selected))

        # Panel snapshot: every live agent tab, visible or buried (also split tabs).
        listed = [sid for sid, r in recs.items() if r.get("state") != states.ENDED
                  and (sid in where or sid in buried_now)]
        if now - names_ts > NAME_REFRESH_S or any(sid not in names for sid in listed):
            t = time.monotonic()
            for sid in listed:
                session = app.get_session_by_id(sid)
                if session is not None:
                    names[sid] = clean_name(await session.async_get_variable("autoName"))
            names_ts = now
            parts["names"] = time.monotonic() - t
        if update_on and now - update_ts > UPDATE_EVERY_S:
            update_ts = now  # also after a failed check: try again tomorrow
            found = await asyncio.to_thread(check_update)
            if found:
                update = found
                tmp = UPDATE_FILE.with_suffix(".tmp")
                tmp.write_text(json.dumps(update))
                tmp.replace(UPDATE_FILE)
            log("update_check", ok=found is not None, available=bool(found and found["available"]))
        if now - usage_ts > USAGE_SCAN_S:
            t = time.monotonic()
            table = await asyncio.to_thread(process_table)
            for sid in listed:
                session = app.get_session_by_id(sid)
                pid = await session.async_get_variable("jobPid") if session is not None else None
                if isinstance(pid, int) and pid > 0:
                    usage[sid] = tree_usage(table, pid)
                    hot[sid] = hot_update(hot_since, sid, recs[sid]["state"], usage[sid][0], now,
                                          args.cpu_hot_pct, args.cpu_hot_min)
            usage_ts = now
            parts["usage"] = time.monotonic() - t
        for sid in listed:
            if now - models.get(sid, (0, None))[0] > MODEL_REFRESH_S:
                path = search.transcript(recs[sid].get("agent", ""), recs[sid].get("agent_session"))
                models[sid] = (now, search.transcript_model(path) if path else None)
        write_panel([{"session": sid, "name": names.get(sid) or Path(recs[sid].get("cwd") or "?").name,
                      "agent": recs[sid].get("agent", ""), "state": recs[sid]["state"],
                      "cwd": recs[sid].get("cwd") or "",
                      "detail": recs[sid].get("detail", ""),
                      "question": recs[sid].get("question") if recs[sid]["state"] == states.WAITING else None,
                      "since": since.get(sid, (None, recs[sid]["ts"]))[1],
                      "last": recs[sid]["ts"],
                      "buried": sid in buried_now, "focused": focus.has(sid),
                      "pinned": sid in pins, "model": models.get(sid, (0, None))[1],
                      "cpu": usage.get(sid, (None, None))[0], "mem_mb": usage.get(sid, (None, None))[1],
                      "hot": hot.get(sid, False),
                      "unseen": recs[sid]["state"] == states.DONE and not focus.has(sid)
                                and last_focus.get(sid, 0) < recs[sid]["ts"]} for sid in listed],
                    args.apply, update=bool(update_on and update.get("available")))

        d = decide(tabs, now, cfg, focus.changed_ts, counts)
        for sid in release:
            d.unbury.append(sid)
            d.reasons[sid] = "raise: agent ended or state unknown"
        sig = (tuple(d.bury), tuple(d.unbury))
        if (d.bury or d.unbury) and sig != last_sig:
            log("decide", apply=args.apply, bury=d.bury, unbury=d.unbury, reasons=d.reasons,
                lag_s={s: round(now - recs[s]["ts"], 2) for s in d.unbury if s in recs},
                focused=focus.session, app_active=focus.app_active,
                focus_changed_s_ago=round(now - focus.changed_ts, 1))
        last_sig = sig
        unbury_now = d.unbury
        if args.apply and d.unbury and keyboard_idle_s() < TYPING_QUIET_S:
            if not deferring:
                log("defer_unbury", sessions=d.unbury, keyboard_idle_s=round(keyboard_idle_s(), 1))
            deferring, unbury_now = True, []
        else:
            deferring = False
        if args.apply and (unbury_now or d.bury):
            t = time.monotonic()
            for sid in unbury_now:
                await unbury(app, sid, ours.get(sid, {}))
                ours.pop(sid, None)
                save_buried(ours)
            for sid in d.bury:
                await bury(app, sid, focus, ours, d.reasons.get(sid, ""))
            parts["act"] = time.monotonic() - t
        tick_s = time.monotonic() - t0
        if tick_s > SLOW_TICK_S:
            log("slow_tick", s=round(tick_s, 2), parts={k: round(v, 2) for k, v in parts.items()},
                sessions=len(where), buried=len(buried_now))
        for _ in range(int(TICK_S / 0.2)):  # wake early for a panel click
            try:
                await asyncio.wait_for(stop.wait(), 0.2)
            except asyncio.TimeoutError:
                pass
            if stop.is_set() or pending_requests():
                break

    if args.apply:
        await restore_all(app)
    log("stop")


DAEMON_DEFAULTS = {"n": ("n_active", 5), "idle_min": ("idle_min", 20.0),
                   "working_park_s": ("working_park_s", 120.0),
                   "cpu_hot_pct": ("cpu_hot_pct", 50.0), "cpu_hot_min": ("cpu_hot_min", 2.0)}


def apply_config(args, config):
    """Fill the options not given as flags from config.json, then the built-in defaults.
    Also add config "agents": {"extra": [...]} to the agent names that seeding looks for."""
    daemon_cfg = config.get("daemon") if isinstance(config.get("daemon"), dict) else {}
    for attr, (key, default) in DAEMON_DEFAULTS.items():
        if getattr(args, attr) is None:
            value = daemon_cfg.get(key, default)
            setattr(args, attr, value if isinstance(value, (int, float)) else default)
    agents_cfg = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    for name in agents_cfg.get("extra") or []:
        if isinstance(name, str) and name and name not in AGENTS:
            AGENTS.append(name)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="really bury and unbury (default: dry run)")
    p.add_argument("--restore", action="store_true", help="unbury every session this tool buried, then exit")
    # Defaults come from config.json "daemon" (see config.example.json); a flag overrides it.
    p.add_argument("--n", type=int, help="max visible agent tabs (default 5)")
    p.add_argument("--idle-min", type=float, help="park a seen done/idle tab after N minutes (default 20)")
    p.add_argument("--working-park-s", type=float,
                   help="park a working tab N s after it lost focus (default 120)")
    p.add_argument("--cpu-hot-pct", type=float,
                   help="mark an agent that is not working but uses this %% CPU or more (default 50; 0: off)")
    p.add_argument("--cpu-hot-min", type=float, help="... for this many minutes (default 2)")
    p.add_argument("--only-window", action="append", default=[],
                   help="manage only this iTerm2 window id (repeatable; for tests)")
    args = p.parse_args()
    apply_config(args, states.load_config())
    ONLY_WINDOWS.update(args.only_window)
    # One daemon (or restore) at a time: two would both edit buried.json.
    states.HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    states.HOME.chmod(0o700)  # state and logs hold commands and questions: this user only
    lock = open(states.HOME / "daemon.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("agent-hover daemon already running (lock held). Use ./run.sh stop first.")
    iterm2.run_until_complete(lambda conn: run(conn, args))


if __name__ == "__main__":
    main()
