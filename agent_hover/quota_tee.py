"""Status-line wrapper: save the agent's quota from the status-line input, then run the real
status-line command with the same input. Usage: quota_tee.py claude|agy [-- COMMAND...]

Claude Code and agy send their usage limits to the status-line command on stdin (Claude:
"rate_limits" five_hour / seven_day; agy: "quota" per bucket). This is the only place they
give them without a request, so `./install.sh` puts this wrapper in front of the status-line
command. It writes ~/.agent-hover/quota/<agent>.json, and the session's model, effort and
context use to ~/.agent-hover/status/<agent>-<session>.json, and prints only what the real
command prints. Standard library only (it runs at every status-line update); it never fails the line.
"""
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("AGENT_HOVER_HOME", Path.home() / ".agent-hover"))
QUOTA_DIR = HOME / "quota"
STATUS_DIR = HOME / "status"
SESSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{7,63}")


def num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def picked(agent, data, now):
    """The quota part of one status-line input, or None."""
    if agent == "claude":
        limits = data.get("rate_limits")
        if not isinstance(limits, dict):
            return None
        windows = {}
        for key in ("five_hour", "seven_day"):
            w = limits.get(key)
            if isinstance(w, dict) and num(w.get("used_percentage")) is not None:
                windows[key] = {"used": num(w["used_percentage"]), "resets_at": num(w.get("resets_at"))}
        return {"windows": windows, "ts": now} if windows else None
    if agent == "agy":
        quota = data.get("quota")
        if not isinstance(quota, dict):
            return None
        buckets = {}
        for key, v in quota.items():
            if isinstance(v, dict) and num(v.get("remaining_fraction")) is not None:
                left = num(v.get("reset_in_seconds"))
                buckets[str(key)[:80]] = {"remaining": num(v["remaining_fraction"]),
                                          "resets_at": now + left if left is not None else None}
        model = data.get("model") if isinstance(data.get("model"), dict) else {}
        name = model.get("id") or model.get("display_name") or ""
        return {"buckets": buckets, "model": str(name)[:120], "ts": now} if buckets else None
    return None


SAME_WINDOW_S = 120  # two readings whose reset times differ less are the same window


def merged_claude(old, new, now):
    """Each Claude session sends the limits it saw at its last request, also an idle one (its
    status line refreshes each minute). Keep per window: a newer window replaces an older one;
    in one window the use only grows, so the higher reading wins. "seen" changes only when the
    value does, so an idle session's repeat does not make old data look new."""
    out = {}
    for key in ("five_hour", "seven_day"):
        o = (old or {}).get("windows", {}).get(key)
        n = new["windows"].get(key)
        o = o if isinstance(o, dict) and (o.get("resets_at") or now + 1) > now else None  # reset since
        if n and n.get("resets_at") is not None and n["resets_at"] <= now:
            n = None  # a reading from a window that has reset already
        if not o or (n and o.get("resets_at") is None):
            pick = {**n, "seen": now} if n else o
        elif not n:
            pick = o
        elif n.get("resets_at") is not None and n["resets_at"] > o["resets_at"] + SAME_WINDOW_S:
            pick = {**n, "seen": now}  # a new window began
        elif n.get("resets_at") is not None and n["resets_at"] < o["resets_at"] - SAME_WINDOW_S:
            pick = o  # an old window from an idle session
        else:
            pick = {**n, "seen": now} if n["used"] > o["used"] else o
        if pick:
            out[key] = pick if pick.get("seen") else {**pick, "seen": (old or {}).get("ts") or now}
    return {"windows": out, "ts": max((w.get("seen") or 0 for w in out.values()), default=now)} if out else None


def session_status(data, now):
    """(session ID, {model, effort, ctx (percent), tok_in, tok_out, ts}) or None."""
    sid = data.get("session_id") or data.get("conversation_id")
    if not isinstance(sid, str) or not SESSION_RE.fullmatch(sid):
        return None
    model = data.get("model") if isinstance(data.get("model"), dict) else {}
    effort = data.get("effort") if isinstance(data.get("effort"), dict) else {}
    ctx = data.get("context_window") if isinstance(data.get("context_window"), dict) else {}
    level = effort.get("level") or model.get("effort")
    status = {"model": str(model.get("display_name") or model.get("id") or "")[:80] or None,
              "effort": str(level)[:20] if level else None, "ctx": num(ctx.get("used_percentage")),
              "tok_in": num(ctx.get("total_input_tokens")), "tok_out": num(ctx.get("total_output_tokens")), "ts": now}
    return sid, status


def write(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}"
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def save(agent, raw):
    if agent not in ("claude", "agy"):
        return
    try:
        data, now = json.loads(raw), time.time()
        found = picked(agent, data, now)
        if found and agent == "claude":
            QUOTA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            with open(QUOTA_DIR / ".claude.lock", "a") as lock:  # sessions refresh at the same time
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    old = json.loads((QUOTA_DIR / "claude.json").read_text())
                except (OSError, ValueError):
                    old = None
                found = merged_claude(old if isinstance(old, dict) else None, found, now)
                if found and found != old:
                    write(QUOTA_DIR / "claude.json", found)
        elif found:
            write(QUOTA_DIR / f"{agent}.json", found)
        status = session_status(data, now)
        if status:
            write(STATUS_DIR / f"{agent}-{status[0]}.json", status[1])
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def main():
    args = sys.argv[1:]
    agent = args[0] if args else ""
    command = args[args.index("--") + 1:] if "--" in args else []
    raw = sys.stdin.buffer.read()
    save(agent, raw)
    if not command:
        return 0
    try:
        return subprocess.run(command, input=raw).returncode
    except OSError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
