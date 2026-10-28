"""The command line each agent session started with, so that a resume repeats its options.

The hook saves it at SessionStart or the first prompt (it walks up to the agent process). The
daemon also saves it each 30 s for running Claude sessions (Claude's ~/.claude/sessions/<pid>.json)
and for Codex tabs (the tab's job; Codex can run its hooks in its app-server, not under the tab). A
resume keeps the options (model, effort, permission mode, ...) and drops the ones that belong
to one start only: the prompt, resume/continue, the session ID, a new worktree.
"""
import ctypes
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from agent_hover import states

COMMANDS = states.HOME / "commands.json"  # session ID -> {"agent", "argv", "pid", "ts"}
KEEP_DAYS = 60

# Claude Code options (claude --help) that take a value; the variadic ones (<x...>) take every
# word up to the next option; [x] takes the next word unless it is an option.
CLAUDE_VALUE = {"--agent", "--agents", "--append-system-prompt", "--append-system-prompt-file", "--autocompact",
                "--debug-file", "--effort", "--environment", "--fallback-model", "--input-format", "--json-schema",
                "--max-budget-usd", "--model", "--output-format", "--permission-mode", "--permission-prompts",
                "--plugin-dir", "--plugin-url", "--remote-control-session-name-prefix", "--session-id",
                "--setting-sources", "--settings", "--system-prompt", "--system-prompt-file",
                "--system-prompt-snapshot", "-n", "--name"}
CLAUDE_MANY = {"--add-dir", "--allowedTools", "--allowed-tools", "--betas", "--disallowedTools",
               "--disallowed-tools", "--file", "--mcp-config", "--tools"}
CLAUDE_OPTIONAL = {"-d", "--debug", "--cloud", "--from-pr", "--prompt-suggestions", "--remote-control", "-r",
                   "--resume", "--teleport", "-w", "--worktree"}
CLAUDE_DROP = {"-r", "--resume", "-c", "--continue", "--session-id", "--fork-session", "-p", "--print", "-n",
               "--name", "-w", "--worktree", "--tmux", "--bg", "--background", "--cloud", "--desktop",
               "--teleport", "--from-pr", "--environment", "--file", "-h", "--help", "-v", "--version"}
# Codex (codex --help, codex resume --help: the same options).
CODEX_VALUE = {"-c", "--config", "--enable", "--disable", "--remote", "--remote-auth-token-env", "-m", "--model",
               "--local-provider", "-p", "--profile", "-s", "--sandbox", "-C", "--cd", "--add-dir", "-a",
               "--ask-for-approval"}
CODEX_MANY = {"-i", "--image"}
CODEX_OPTIONAL = set()
CODEX_DROP = {"-C", "--cd", "--worktree", "-i", "--image", "--last", "--all", "--include-non-interactive", "-h",
              "--help", "-V", "--version"}
TABLES = {"claude": (CLAUDE_VALUE, CLAUDE_MANY, CLAUDE_OPTIONAL, CLAUDE_DROP),
          "codex": (CODEX_VALUE, CODEX_MANY, CODEX_OPTIONAL, CODEX_DROP)}


def process_argv(pid):
    """The argument list a running process started with (exact, not split from ps), or None."""
    try:
        pid = int(pid)
        if Path("/proc").is_dir():
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
            return [a.decode("utf-8", "replace") for a in raw.split(b"\0")[:-1]] or None
        if sys.platform != "darwin":
            return None
        libc = ctypes.CDLL(None, use_errno=True)
        argmax, size = ctypes.c_int(0), ctypes.c_size_t(ctypes.sizeof(ctypes.c_int))
        if libc.sysctl((ctypes.c_int * 2)(1, 8), 2, ctypes.byref(argmax), ctypes.byref(size), None, 0) != 0:
            return None  # CTL_KERN, KERN_ARGMAX
        buf, size = ctypes.create_string_buffer(argmax.value), ctypes.c_size_t(argmax.value)
        if libc.sysctl((ctypes.c_int * 3)(1, 49, pid), 3, buf, ctypes.byref(size), None, 0) != 0:
            return None  # KERN_PROCARGS2: argc, the program path, NUL padding, then argv
        raw = buf.raw[:size.value]
        argc = int.from_bytes(raw[:4], sys.byteorder)
        rest = raw[4:].partition(b"\0")[2].lstrip(b"\0")
        return [a.decode("utf-8", "replace") for a in rest.split(b"\0")[:argc]] or None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


NOT_A_SESSION = {"app-server", "exec", "e", "mcp", "mcp-server", "login", "logout", "cloud", "apply", "sandbox",
                 "completion", "features", "doctor", "update", "plugin", "config", "setup-token", "install"}


def option_groups(agent, words):
    """[(option name or None, [words])]: each option with its values, and each other word (the
    prompt, a subcommand, a session ID) alone with None. Words after "--" are the prompt."""
    value, many, optional, _ = TABLES.get(agent, (set(), set(), set(), set()))
    out, k = [], 0
    while k < len(words):
        word = words[k]
        k += 1
        if word == "--":
            out += [(None, [w]) for w in words[k:]]
            break
        if not word.startswith("-") or word == "-":
            out.append((None, [word]))
            continue
        name, attached = (word.split("=", 1)[0], True) if "=" in word else (word, False)
        if not attached and not name.startswith("--") and len(name) > 2 and name[:2] in value:
            name, attached = name[:2], True  # -mgpt-5
        group = [word]
        if not attached:
            if name in value and k < len(words):
                group.append(words[k])
                k += 1
            elif name in many:
                while k < len(words) and not words[k].startswith("-"):
                    group.append(words[k])
                    k += 1
            elif name in optional and k < len(words) and not words[k].startswith("-"):
                group.append(words[k])
                k += 1
        out.append((name, group))
    return out


def agent_index(agent, argv):
    """Index of the agent program in argv ("claude ...", "node /x/bin/codex ..."), or None. None
    too for a command that is no interactive session ("codex app-server", "claude mcp ...")."""
    for i, word in enumerate((argv or [])[:3]):
        if Path(word).name == agent:
            groups = option_groups(agent, argv[i + 1:])
            first = next((g[0] for name, g in groups if name is None), None)
            printed = agent == "claude" and any(name in ("-p", "--print") for name, _ in groups)
            return None if first in NOT_A_SESSION or printed else i
    return None


def resume_args(agent, argv):
    """The options of an agent's command line that a resume repeats, in their order."""
    i = agent_index(agent, argv)
    if i is None or agent not in TABLES:
        return []
    drop = TABLES[agent][3]
    return [w for name, group in option_groups(agent, argv[i + 1:]) if name and name not in drop for w in group]


def load():
    try:
        data = json.loads(COMMANDS.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data):
    states.ensure_dir(COMMANDS.parent)
    tmp = COMMANDS.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, COMMANDS)


def remember(found, now=None):
    """Save {session ID: (agent, pid)}: the command line of each pid, when it is new for that
    session (a later resume with other options replaces it). Hooks and the daemon both write:
    the read and the write are done under one file lock."""
    now = time.time() if now is None else now
    states.ensure_dir(COMMANDS.parent)
    with open(COMMANDS.with_suffix(".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _remember(found, now)


def _remember(found, now):
    data, changed = load(), False
    for sid, (agent, pid) in found.items():
        if not states.UUID_RE.fullmatch(str(sid)) or data.get(sid, {}).get("pid") == pid:
            continue
        argv = process_argv(pid)
        if agent_index(agent, argv) is None:
            continue
        data[sid] = {"agent": agent, "argv": argv, "pid": pid, "ts": now}
        changed = True
    old = [sid for sid, v in data.items() if not isinstance(v, dict) or now - v.get("ts", 0) > KEEP_DAYS * 86400]
    for sid in old:
        del data[sid]
    if changed or old:
        save(data)


def parent_pids(pid, depth=6):
    """pid, its parent, and so on (one ps call)."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return [pid]
    parent = {}
    for line in out.split("\n"):
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            parent[int(parts[0])] = int(parts[1])
    chain = [pid]
    while len(chain) < depth and parent.get(chain[-1], 0) > 1:
        chain.append(parent[chain[-1]])
    return chain


def remember_from_hook(agent, session_id, pid):
    """The hook's SessionStart: save the command line of the agent process above the hook."""
    for p in parent_pids(pid):
        if agent_index(agent, process_argv(p)) is not None:
            remember({session_id: (agent, p)})
            return


def resume_words(agent, session_id):
    """The resume command for a session as a word list, with its saved options."""
    saved = load().get(session_id) or {}
    opts = resume_args(agent, saved.get("argv")) if saved.get("agent") == agent else []
    if agent == "claude":
        return ["claude", *opts, "--resume", session_id]
    return ["codex", "resume", *opts, session_id]
