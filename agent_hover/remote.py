"""Remote hosts: agents in the tmux panes of other machines, shown in the panel next to the local tabs.

Read-only, nothing to install: each interval the daemon runs one shell script on the host
(through the "connect" command) that only reads. It lists the tmux panes, runs ps, reads Claude
Code's own status files (~/.claude/sessions/<pid>.json) and the end of each running Codex
session file. Codex writes nothing when it waits for an approval, and agy keeps its state in
files the script does not read, so the script also reads the last screen lines of each Codex
and agy pane (tmux capture-pane) and looks for their prompts (agy: also its working footer).
`python -m agent_hover.remote script` prints the exact script. Only a click on a
remote row changes something: in a local iTerm2 tab already attached to that tmux session, it
selects the row's tmux window and pane; else it opens a tab with the "attach" command. A tab
the person attached by hand is found when its ssh connection is the one a tmux client of that
session came in on (SSH_CONNECTION, Linux hosts).

config.json (no "remote" hosts: nothing connects):
  "remote": {"hosts": [
    {"name": "gpu-box",                 # shown in the panel
     "connect": ["ssh", "gpu-box"],     # needed: runs the script, connect + [one command]
     "attach": "ssh -t gpu-box 'tmux attach -t {pane} \\; select-window -t {pane} \\; select-pane -t {pane}'",
     "interval_s": 5}
  ]}
"connect" can be any command that runs its last argument as a shell command on the host (ssh
with options, a jump host, tailscale ssh). To an ssh command the daemon adds BatchMode,
ConnectTimeout, Compression and a shared connection (ControlMaster), each only when the command
does not set it ("share_connection": false: no shared connection). "attach" is typed into the
new local tab; {pane} {session} {window} {host} are filled in. Without an "attach", it comes
from an ssh "connect" (ssh ... -t HOST 'tmux attach ...').

  python -m agent_hover.remote script            the script the daemon runs on each host
  python -m agent_hover.remote check NAME        run it once on a host and print the rows
"""
import argparse
import json
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

from agent_hover import agents, states

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")
PANE_RE = re.compile(r"%\d+")
KEY_RE = re.compile(r"tmux-(\d+)-(\d+)")
MARK = "@@section"
# The pane title is last: it is split off last, so it can hold any text (also the separator).
FIELDS = ["pid", "pane_id", "session_name", "window_index", "pane_pid", "pane_active", "window_active",
          "session_attached", "pane_current_path", "window_name", "pane_title"]
SEP = "|~|"  # tmux prints control characters as \ooo, so not \x1f
CODEX_TAIL = 65536  # bytes of each Codex session file per scan: the last turn record and limits
MODEL_EVERY_S = 60.0
ATTACH_TMUX = "tmux attach -t {pane} \\; select-window -t {pane} \\; select-pane -t {pane}"
SCREEN_LINES = 30  # last non-empty screen lines of a Codex or agy pane per scan
# Codex prompts that wait for the person (approval, question, trust), from herdr's Codex
# detection rules (github.com/herdrdev/herdr, Apache-2.0). Codex sets its terminal title (the
# tmux pane title) to "Action Required" while it waits; older Codex versions only show the
# prompt. Only the bottom lines are read: older output can quote the same words.
CODEX_TITLE_BLOCKED = "Action Required"
CODEX_BLOCKERS = ("press enter to confirm or esc to cancel", "enter to submit answer", "enter to submit all",
                  "allow command?", "do you trust the contents of this directory?", "trust this folder?")
CODEX_BLOCKER_LINES = 20
# agy (Antigravity CLI) screens, from herdr's antigravity rules (same source): a dialog replaces
# the prompt box and ends with a key-hint row above the "esc to cancel" footer; a working turn
# shows "esc to cancel" under the prompt box, or a braille spinner above it.
AGY_DIALOGS = ((("navigate", "tab amend"), 2, "approve"),  # permission: "↑/↓ Navigate · tab Amend · ..."
               (("navigate", "esc skip"), 2, "answer"),  # question
               (("do you trust the contents of this project?", "yes, i trust this folder", "enter confirm"), 8,
                "trust"))
AGY_WORKING_FOOTER = re.compile(r"(?m)^\s*─{3,}\s*esc to cancel\b")
AGY_SPINNER = re.compile(r"(?m)^\s*[\u2801-\u28FF]+\s+\S")
AGY_TITLE = "agi"  # agy's fixed terminal title: no name
SHELLS = {"zsh", "bash", "fish", "sh", "dash", "-zsh", "-bash", "login"}

# Each running Codex (not `codex exec`): its open session files whose first line says
# "thread_source":"user" (sub-agents have their own files). /proc on Linux, lsof elsewhere.
CODEX_PART = r"""for p in $(ps -A -o pid=,args= | awk '{a=$2; sub(".*/","",a); b=$3; sub(".*/","",b); if ((a=="codex" || b=="codex") && $0 !~ / exec( |$)/) print $1}'); do
  if [ -d /proc/$p/fd ]; then files=$(ls -l /proc/$p/fd 2>/dev/null | sed -n 's#.* -> \(/.*/\.codex/sessions/.*/rollout-[^/]*\.jsonl\)$#\1#p' | sort -u)
  else files=$(lsof -p $p -Fn 2>/dev/null | sed -n 's#^n\(/.*/\.codex/sessions/.*/rollout-[^/]*\.jsonl\)$#\1#p' | sort -u); fi
  for f in $files; do
    head -c 8192 "$f" | head -n 1 | grep -q '"thread_source": *"user"' || continue
    echo "$S codex $p $(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null) $f"
    tail -c TAIL "$f"; echo
  done
done"""

# The tmux clients and the ssh connection each one came in on: its SSH_CONNECTION
# ("client-ip client-port server-ip server-port", from /proc, so Linux only), and the age in
# seconds of its ssh session (the process under sshd). A local tab whose own ssh has that
# address and port is the tab of that client; tabs that share one ControlMaster also share its
# connection, and then the age tells them apart.
CLIENTS_SCRIPT = r"""tmux list-clients -F '#{client_pid}|~|#{client_session}' 2>/dev/null
echo @@section ssh
for p in $(tmux list-clients -F '#{client_pid}' 2>/dev/null); do
  [ -r /proc/$p/environ ] || continue
  q=$p
  while :; do
    pp=$(ps -o ppid= -p $q | tr -d ' ')
    { [ -z "$pp" ] || [ "$pp" -le 1 ]; } && break
    case "$(ps -o comm= -p $pp)" in sshd*) break;; esac
    q=$pp
  done
  echo "$p $(ps -o etimes= -p $q | tr -d ' ') $(tr '\0' '\n' < /proc/$p/environ | grep '^SSH_CONNECTION=')"
done"""
AGE_SLACK_S = 10.0  # a local ssh and the remote session it started: ages this close


def tmux_key(server_pid, pane_id):
    """'tmux-<server pid>-<pane number>' for pane '%4'. Pane numbers start again when the tmux
    server restarts; the server's process ID keeps them apart. None for a bad value."""
    m = re.fullmatch(r"%(\d+)", str(pane_id or ""))
    pid = str(server_pid or "")
    return f"tmux-{pid}-{m.group(1)}" if m and pid.isdigit() else None


def split_sid(sid):
    """'gpu-box:tmux-4012-4' -> ('gpu-box', 'tmux-4012-4'); None for a local session."""
    host, _, key = str(sid).partition(":")
    return (host, key) if NAME_RE.fullmatch(host) and KEY_RE.fullmatch(key) else None


def parse_clients(text):
    """[(tmux session, client ip, client port, session age in s or None)] of the clients that
    came in over ssh."""
    sessions, conns, part = {}, {}, "clients"
    for line in text.splitlines():
        if line == f"{MARK} ssh":
            part = "ssh"
        elif part == "clients" and SEP in line:
            pid, session = line.split(SEP, 1)
            sessions[pid] = session
        elif part == "ssh":
            m = re.fullmatch(r"(\d+) +(\d+)? *SSH_CONNECTION=(\S+) (\d+) \S+ \d+", line.strip())
            if m:
                conns[m[1]] = (m[3], int(m[4]), float(m[2]) if m[2] else None)
    return [(sessions[pid], *conns[pid]) for pid in sessions if pid in conns]


def elapsed_s(text):
    """Seconds of a ps etime value, [[dd-]hh:]mm:ss, or None."""
    days, _, rest = text.strip().rpartition("-")
    parts = rest.split(":")
    if not (1 < len(parts) < 4 and all(x.isdigit() for x in parts) and (not days or days.isdigit())):
        return None
    secs = 0
    for x in parts:
        secs = secs * 60 + int(x)
    return float(secs + int(days or 0) * 86400)


def local_ssh_ends(ttys):
    """{tty: {(local ip, local port): age in s}} of the ssh processes on these ttys ("/dev/ttys003"):
    the TCP connection each one has, or, for one that goes through a ControlMaster (a unix
    socket to the master process), the master's."""
    try:
        ps = subprocess.run(["ps", "-axo", "pid=,etime=,tty=,comm="], capture_output=True, text=True,
                            timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    ssh, tabs = [], {}
    for line in ps.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and Path(parts[3].split()[0].rstrip(":")).name == "ssh":  # a master: "ssh: /path [mux]"
            ssh.append(parts[0])
            if f"/dev/{parts[2]}" in ttys:
                tabs[parts[0]] = (f"/dev/{parts[2]}", elapsed_s(parts[1]))
    if not tabs:
        return {}
    try:
        out = subprocess.run(["lsof", "-nP", "-p", ",".join(ssh), "-Fptdn"], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    tcp, peers, owner = {}, {}, {}  # pid -> {(ip, port)}; pid -> {peer socket}; socket -> pid
    pid = kind = dev = None
    for line in out.splitlines():
        tag, value = line[:1], line[1:]
        if tag == "p":
            pid = value
        elif tag == "f":
            kind = dev = None
        elif tag == "t":
            kind = value
        elif tag == "d":
            dev = value
        elif tag == "n" and kind in ("IPv4", "IPv6") and "->" in value:
            ip, _, port = value.split("->")[0].rpartition(":")
            if port.isdigit():
                tcp.setdefault(pid, set()).add((ip.strip("[]"), int(port)))
        elif tag == "n" and kind == "unix":
            if dev:
                owner[dev] = pid
            if value.startswith("->"):
                peers.setdefault(pid, set()).add(value[2:])
    ends = {}
    for pid, (tty, age) in tabs.items():
        found = set(tcp.get(pid, ()))
        for peer in peers.get(pid, ()):
            if owner.get(peer) not in (None, pid):
                found |= tcp.get(owner[peer], set())
        for end in found:
            ends.setdefault(tty, {})[end] = age
    return ends


def match_tab(clients, local, tmux_session):
    """The tty of the local tab where a tmux client of this session runs, or None. A client
    matches the tab whose ssh has the client's connection. When several tabs (one ControlMaster)
    or several clients share that connection: the tab whose ssh age is closest to the age of the
    client's ssh session, within AGE_SLACK_S."""
    for session, ip, port, age in clients:
        if session != tmux_session:
            continue
        end = (ip, port)
        tabs = {tty: ends[end] for tty, ends in local.items() if end in ends}
        if len(tabs) == 1 and sum((c[1], c[2]) == end for c in clients) == 1:
            return next(iter(tabs))
        near = [(abs(a - age), tty) for tty, a in tabs.items()
                if age is not None and a is not None and abs(a - age) <= AGE_SLACK_S]
        if near:
            return min(near)[1]
    return None


def collect_script(models=(), screens=()):
    """The read-only script for one scan. models: ("claude" | "codex", session ID, file or None)
    whose model line to read this time (once a minute). screens: tmux panes whose last screen
    lines to read (the Codex panes of the last scan)."""
    fmt = SEP.join("#{%s}" % f for f in FIELDS)
    lines = [f"S={MARK}",
             'echo "$S host $(hostname)"',
             'echo "$S panes"', f"tmux list-panes -a -F {shlex.quote(fmt)} 2>/dev/null",
             # Agent detection needs only the first two words of each command line.
             'echo "$S ps"', "ps -A -o pid=,ppid=,pcpu=,rss=,comm=,args= 2>/dev/null | awk '{print $1,$2,$3,$4,$5,$6,$7}'",
             'for f in "$HOME"/.claude/sessions/*.json; do [ -f "$f" ] && { echo "$S claude"; cat "$f"; echo; }; done',
             CODEX_PART.replace("TAIL", str(CODEX_TAIL))]
    for agent, sid, path in models:
        if not states.UUID_RE.fullmatch(str(sid)):
            continue
        if agent == "claude":
            lines.append(f'for f in "$HOME"/.claude/projects/*/{sid}.jsonl; do [ -f "$f" ] && '
                         f'{{ echo "$S model {sid}"; tail -c 1048576 "$f" | grep -o \'"model": *"[^"]*"\' | tail -n 1; }}; done')
        elif agent == "codex" and path:
            lines.append(f'echo "$S model {sid}"; grep -o \'"model": *"[^"]*"\' {shlex.quote(path)} 2>/dev/null | tail -n 1')
    for pane in screens:
        if PANE_RE.fullmatch(str(pane)):
            lines.append(f'echo "$S screen {pane}"; tmux capture-pane -p -J -t {pane} 2>/dev/null'
                         f" | awk 'NF' | tail -n {SCREEN_LINES}")
    lines.append('echo "$S end"')
    return "\n".join(lines) + "\n"


def parse_output(text):
    """The script's output -> {"host", "panes", "ps", "claude", "codex", "models", "screens", "complete"}."""
    out = {"host": "", "panes": [], "ps": {}, "claude": [], "codex": [], "models": {}, "screens": {},
           "complete": False}
    section, body = None, []

    def close():
        if section is None:
            return
        kind, args = section
        if kind == "panes":
            for line in body:
                parts = line.split(SEP, len(FIELDS) - 1)
                if len(parts) == len(FIELDS):
                    out["panes"].append(dict(zip(FIELDS, parts)))
        elif kind == "ps":
            for line in body:
                parts = line.split(None, 5)
                try:
                    out["ps"][int(parts[0])] = (int(parts[1]), float(parts[2]), int(parts[3]), parts[4],
                                                parts[5] if len(parts) > 5 else "")
                except (IndexError, ValueError):
                    continue
        elif kind == "claude":
            try:
                data = json.loads("\n".join(body))
            except ValueError:
                return
            if isinstance(data, dict):
                out["claude"].append(data)
        elif kind == "codex":
            parts = args.split(None, 2)
            if len(parts) == 3 and parts[0].isdigit():
                out["codex"].append({"pid": int(parts[0]), "mtime": float(parts[1]) if parts[1].isdigit() else 0.0,
                                     "path": parts[2], "tail": "\n".join(body)})
        elif kind == "model":
            m = re.search(r'"model": *"([^"]{1,80})"', "\n".join(body))
            if m and states.UUID_RE.fullmatch(args.strip()):
                out["models"][args.strip()] = m.group(1)
        elif kind == "screen":
            if PANE_RE.fullmatch(args.strip()):
                out["screens"][args.strip()] = body[-SCREEN_LINES:]

    for line in text.splitlines():
        if line.startswith(MARK + " "):
            close()
            kind, _, args = line[len(MARK) + 1:].partition(" ")
            if kind == "host":
                out["host"], section = args.strip(), None
            elif kind == "end":
                out["complete"], section = True, None
            else:
                section = (kind, args)
            body = []
        elif section is not None:
            body.append(line)
    close()
    return out


def screen_text(lines):
    """The panel's question text from screen lines: the last ones, long space runs cut."""
    return "\n".join(re.sub(r" {3,}", "  ", line) for line in lines[-CODEX_BLOCKER_LINES:])[-1500:]


def codex_blocker(title, screen):
    """The prompt text when a Codex pane waits for the person (its title or its last screen
    lines show an approval, question or trust prompt), else None."""
    bottom = [line.rstrip() for line in (screen or []) if line.strip()][-CODEX_BLOCKER_LINES:]
    text = "\n".join(bottom)
    low = text.lower()
    update = "update available" in low and low.endswith(("esc skip", "press enter to continue"))  # start-up picker
    if CODEX_TITLE_BLOCKED in (title or "") or update or any(b in low for b in CODEX_BLOCKERS):
        return screen_text(bottom) or "Codex waits for you (see the pane)."
    return None


def agy_screen_state(screen):
    """(state, detail, question) of an agy pane from its last screen lines, or None (no screen
    read yet). A prompt waits, the working footer or a spinner works, else idle."""
    if screen is None:
        return None
    lines = [line.rstrip() for line in screen if line.strip()]
    for words, last, detail in AGY_DIALOGS:
        if all(w in "\n".join(lines[-last:]).lower() for w in words):
            return states.WAITING, detail, screen_text(lines)
    whole = "\n".join(lines).lower()
    if "requesting permission for:" in whole and ("do you want to proceed?" in whole
                                                  or ("tab amend" in whole and "edit command" in whole)):
        return states.WAITING, "approve", screen_text(lines)  # before agy 1.1.28
    if AGY_WORKING_FOOTER.search("\n".join(lines[-2:]).lower()) or AGY_SPINNER.search("\n".join(lines[-12:])):
        return states.WORKING, "", None
    return states.IDLE, "", None


def build_rows(parsed, now):
    """key -> row for each tmux pane with an agent. A Claude row comes from Claude's own status
    file (status, folder, session ID, pane); a Codex row from the end of its session file; any
    other agent process in a pane from the pane title (Claude's glyph) only. A Codex pane whose
    title or screen shows a prompt waits (codex_blocker); an agy pane's state comes from its
    screen (agy_screen_state)."""
    table = parsed["ps"]
    children = {}
    for pid, row in table.items():
        children.setdefault(row[0], []).append(pid)
    panes = {p["pane_id"]: p for p in parsed["panes"] if PANE_RE.fullmatch(p["pane_id"])}
    shells = {}
    for p in panes.values():
        if p["pane_pid"].isdigit():
            shells[int(p["pane_pid"])] = p["pane_id"]

    def pane_of(pid):
        for _ in range(64):
            if pid in shells:
                return shells[pid]
            if pid not in table or table[pid][0] == pid:
                return None
            pid = table[pid][0]
        return None

    usage = {pid: row[:3] for pid, row in table.items()}
    found = {}  # pane -> row fields

    for info in parsed["claude"]:
        pid = info.get("pid")
        if not isinstance(pid, int) or pid not in table or info.get("entrypoint") == "sdk-cli":
            continue  # a stale file, or `claude -p`
        if agents.detect_agent(table[pid][3].rsplit("/", 1)[-1], table[pid][4]) != "claude":
            continue
        tm = str(info.get("tmux") or "")
        pane = tm.rsplit(".", 1)[-1] if "." in tm else ""
        pane = pane if pane in panes else pane_of(pid)
        if not pane:
            continue
        changed = info.get("statusUpdatedAt")
        sid = info.get("sessionId")
        found[pane] = {"agent": "claude", "pid": pid,
                       "state": agents.CLAUDE_STATUS.get(info.get("status"))
                                or agents.title_state(panes[pane]["pane_title"]),
                       "since": changed / 1000 if isinstance(changed, (int, float)) else now,
                       "cwd": info.get("cwd") if isinstance(info.get("cwd"), str) else "",
                       "agent_session": sid if isinstance(sid, str) and states.UUID_RE.fullmatch(sid) else None,
                       "name": info.get("name") if isinstance(info.get("name"), str) else ""}

    for c in parsed["codex"]:
        pane = pane_of(c["pid"])
        if not pane or pane in found:
            continue
        turn = states.codex_last_turn(None, text=c["tail"])
        seen = states.codex_seed_state(turn)
        if seen:
            state, detail, question = seen
        else:  # no turn record in the tail: a long turn still writes, an old file is idle
            state, detail, question = (states.WORKING if now - c["mtime"] < 120 else states.IDLE), "", None
        when = (turn or {}).get("completed_at") or (turn or {}).get("started_at") or c["mtime"] or now
        sid = Path(c["path"]).stem[-36:]
        found[pane] = {"agent": "codex", "pid": c["pid"], "state": state, "detail": detail, "question": question,
                       "since": float(when) if isinstance(when, (int, float)) else now,
                       "cwd": panes[pane]["pane_current_path"], "path": c["path"],
                       "agent_session": sid if states.UUID_RE.fullmatch(sid) else None,
                       "resets_at": states.codex_resets_at(None, text=c["tail"]) if detail == "usage limit" else None}

    for pane, p in panes.items():  # an agent with no status file (an older Claude, another agent)
        if pane in found or not p["pane_pid"].isdigit():
            continue
        todo, seen_pids = [int(p["pane_pid"])], set()
        while todo:
            pid = todo.pop(0)
            if pid in seen_pids or pid not in table:
                continue
            seen_pids.add(pid)
            agent = agents.detect_agent(table[pid][3].rsplit("/", 1)[-1], table[pid][4])
            if agent:
                found[pane] = {"agent": agent, "pid": pid, "state": agents.title_state(p["pane_title"]),
                               "since": now, "cwd": p["pane_current_path"], "agent_session": None}
                break
            todo.extend(sorted(children.get(pid, [])))

    for pane, f in found.items():
        if f["agent"] == "codex" and f["state"] in (states.WORKING, states.IDLE):
            question = codex_blocker(panes[pane]["pane_title"], parsed["screens"].get(pane))
            if question:
                f.update(state=states.WAITING, detail="approve", question=question, screen=True)
        elif f["agent"] == "agy":
            seen = agy_screen_state(parsed["screens"].get(pane))
            if seen:
                f.update(state=seen[0], detail=seen[1], question=seen[2], screen=True)

    rows = {}
    host = parsed["host"].split(".")[0]
    for pane, f in found.items():
        p = panes[pane]
        key = tmux_key(p["pid"], pane)
        if not key:
            continue
        title = agents.clean_name(p["pane_title"])
        if title.split(".")[0] == host or (f["agent"] == "agy" and title == AGY_TITLE):
            title = ""  # tmux's default title (the host name), or agy's fixed one
        cpu, mem = agents.tree_usage(usage, f["pid"], children)
        waiting = f["state"] == states.WAITING
        rows[key] = {"key": key, "pane": pane, "tmux_session": p["session_name"], "window": p["window_index"],
                     "window_name": p["window_name"], "name": title or f.get("name") or Path(f["cwd"] or "?").name,
                     "agent": f["agent"], "state": f["state"], "detail": f.get("detail", ""), "cwd": f["cwd"],
                     "question": f.get("question") if waiting else None,
                     "resets_at": f.get("resets_at") if waiting else None,
                     "since": f["since"], "agent_session": f.get("agent_session"), "path": f.get("path"),
                     "screen_state": bool(f.get("screen")),
                     "cpu": cpu, "mem_mb": mem,
                     "active": p["pane_active"] == "1" and p["window_active"] == "1"
                               and p["session_attached"] not in ("", "0")}
    return rows


def ssh_options(share):
    """Options added to an ssh connect command, each only when the command does not set it:
    no password prompt (the daemon has no terminal), a short connect timeout, compression, and
    one shared connection (ControlMaster) for all scans and peeks: a scan then needs no new
    login. The control socket is in ~/.agent-hover (ssh makes it private)."""
    opts = {"BatchMode": "yes", "ConnectTimeout": "5", "Compression": "yes"}
    if share:
        opts.update(ControlMaster="auto", ControlPath=f"{states.HOME}/ssh-%C", ControlPersist="120")
    return opts


def with_ssh_options(connect, share=True):
    if Path(connect[0]).name != "ssh":
        return list(connect)
    given = " ".join(connect).lower()
    added = []
    for key, value in ssh_options(share).items():
        if key.lower() not in given:
            added += ["-o", f"{key}={value}"]
    return [connect[0]] + added + connect[1:]


class Host:
    """One remote host: a thread scans it each interval; peek and select run on request."""

    def __init__(self, cfg, log=None):
        self.name = cfg["name"]
        self.user_connect = list(cfg["connect"])
        self.connect = with_ssh_options(self.user_connect, cfg.get("share_connection", True) is not False)
        attach = cfg.get("attach")
        self.attach = attach if isinstance(attach, str) and attach else self.default_attach()
        interval = cfg.get("interval_s", 5)
        self.interval = float(interval) if isinstance(interval, (int, float)) and interval >= 1 else 5.0
        self.log = log or (lambda kind, **kw: None)
        self.rows, self.ts, self.online = {}, 0.0, False
        self.models, self.models_ts = {}, 0.0  # agent session -> model
        self.lock = threading.Lock()
        self.stopped = threading.Event()

    def default_attach(self):
        """'ssh [options] -t HOST <tmux attach ...>' from an ssh connect command, else None."""
        if Path(self.user_connect[0]).name != "ssh":
            return None
        return shlex.join(self.user_connect[:-1] + ["-t", self.user_connect[-1]]) + " " + shlex.quote(ATTACH_TMUX)

    def run(self, script, timeout=20.0):
        """(exit code, output) of a script run with sh on the host; code None on a timeout."""
        try:
            r = subprocess.run(self.connect + ["sh -c " + shlex.quote(script)], stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None, ""
        except OSError as e:
            return None, str(e)
        return r.returncode, r.stdout.decode("utf-8", "replace") + (
            "" if r.returncode == 0 else "\n" + r.stderr.decode("utf-8", "replace")[-300:])

    def scan(self, now=None):
        """One scan: True if the host answered."""
        now = time.time() if now is None else now
        with self.lock:
            rows = list(self.rows.values())
        want = [(r["agent"], r["agent_session"], r.get("path")) for r in rows if r.get("agent_session")]
        due = now - self.models_ts > MODEL_EVERY_S or any(r["agent_session"] not in self.models
                                                         for r in rows if r.get("agent_session"))
        screens = [r["pane"] for r in rows if r["agent"] in ("codex", "agy")]
        code, out = self.run(collect_script(want if due else (), screens))
        parsed = parse_output(out) if code == 0 else None
        if not parsed or not parsed["complete"]:
            with self.lock:
                was, self.online = self.online, False
            if was or not self.ts:
                self.log("remote_down", host=self.name, code=code, out=out.strip()[-300:])
                self.ts = self.ts or -1.0
            return False
        rows = build_rows(parsed, now)
        with self.lock:
            for key, r in rows.items():  # a state seen on the screen lasts since the first scan that saw it
                old = self.rows.get(key)
                if r["screen_state"]:
                    same = (old and old.get("screen_state") and old["state"] == r["state"]
                            and old.get("question") == r.get("question"))  # a new prompt: a new time
                    r["since"] = old["since"] if same else now
            if due:  # a session with no model line is asked again only at the next minute
                for _, sid, _ in want:
                    self.models[sid] = parsed["models"].get(sid, self.models.get(sid))
                self.models_ts = now
            for r in rows.values():
                r["model"] = self.models.get(r.get("agent_session"))
            was, self.rows, self.ts, self.online = self.online, rows, now, True
        if not was:
            self.log("remote_up", host=self.name, rows=len(rows))
        return True

    def start(self):
        self.log("remote_start", host=self.name, connect=self.connect, script=collect_script())
        threading.Thread(target=self._loop, name=f"remote-{self.name}", daemon=True).start()

    def _loop(self):
        fails = 0
        while not self.stopped.is_set():
            try:
                ok = self.scan()
            except Exception as e:  # noqa: BLE001 - a parse bug must not end the scans
                self.log("remote_error", host=self.name, error=f"{type(e).__name__}: {e}"[:300])
                ok = False
            fails = 0 if ok else fails + 1
            self.stopped.wait(self.interval if ok else min(self.interval * 2 ** fails, 120.0))

    def stop(self):
        self.stopped.set()

    def fresh_rows(self, now=None):
        """The last scan's rows, or {} when it is too old (host down)."""
        now = time.time() if now is None else now
        with self.lock:
            return dict(self.rows) if now - self.ts <= max(3 * self.interval, 30.0) else {}

    def peek(self, pane):
        """The last screen lines of a pane (tmux capture-pane), or []."""
        if not PANE_RE.fullmatch(str(pane)):
            return []
        self.log("remote_command", host=self.name, command=f"tmux capture-pane -p -J -t {pane}")
        code, out = self.run(f"tmux capture-pane -p -J -t {pane}", timeout=10)
        return [line.rstrip() for line in out.splitlines()] if code == 0 else []

    def select(self, pane):
        """Make the pane the current one of its tmux window and session (a click on its row)."""
        if PANE_RE.fullmatch(str(pane)):
            command = f"tmux select-window -t {pane} \\; select-pane -t {pane}"
            self.log("remote_command", host=self.name, command=command)
            self.run(command, timeout=10)

    def clients(self):
        """The host's tmux clients now (see parse_clients)."""
        code, out = self.run(CLIENTS_SCRIPT, timeout=10)
        return parse_clients(out) if code == 0 else []

    def attach_command(self, row):
        """The attach command for one row, or None (no "attach" for a non-ssh connect)."""
        if not self.attach or not PANE_RE.fullmatch(str(row.get("pane", ""))):
            return None
        window = str(row.get("window", ""))
        try:
            return self.attach.format(host=self.name, pane=row["pane"], window=window if window.isdigit() else "",
                                      session=shlex.quote(str(row.get("tmux_session", ""))))
        except (KeyError, IndexError, ValueError):
            return None


def load_hosts(config, log=None):
    """Hosts from config.json "remote": {"hosts": [...]}. A host needs a "name" and a "connect"
    command; without them it is skipped, and nothing connects. None by default."""
    remote = config.get("remote") if isinstance(config.get("remote"), dict) else {}
    hosts, names = [], set()
    say = log or (lambda kind, **kw: print(kind, kw, file=sys.stderr))
    for cfg in remote.get("hosts") or []:
        if not isinstance(cfg, dict) or cfg.get("enabled", True) is False:
            continue
        name, connect = cfg.get("name"), cfg.get("connect")
        if not (isinstance(name, str) and NAME_RE.fullmatch(name) and name not in names):
            say("remote_skip", why="bad or repeated name", name=str(name)[:64])
            continue
        if not (isinstance(connect, list) and connect and all(isinstance(x, str) and x for x in connect)):
            say("remote_skip", why="no connect command", name=name)
            continue  # no connect command, no connection
        names.add(name)
        hosts.append(Host(cfg, log=log))
    return hosts


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=["script", "check"])
    p.add_argument("name", nargs="?")
    args = p.parse_args()
    if args.action == "script":
        print(collect_script())
        return
    host = next((h for h in load_hosts(states.load_config()) if h.name == args.name), None)
    if host is None:
        sys.exit(f'No host "{args.name}" in {states.HOME / "config.json"} "remote": {{"hosts": [...]}}')
    if not host.scan() or not host.scan(time.time()):  # the second scan reads the models
        sys.exit(f"{host.name}: no answer (see the command above, or run it by hand)")
    print(f"{host.name}: {len(host.rows)} agent pane(s); connect: {shlex.join(host.connect)}")
    for r in host.rows.values():
        print(f"  {r['tmux_session']}:{r['window']} {r['pane']:<5} {r['agent']:<7} {r['state']:<8} "
              f"{r['name'][:40]:<40} {r.get('model') or ''}")
    print(f"attach: {host.attach_command(next(iter(host.rows.values()))) if host.rows else host.attach}")


if __name__ == "__main__":
    main()
