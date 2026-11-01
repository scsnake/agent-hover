"""Usage limits (quota) of each agent for the panel's hover frame. Read only from what the
agents already give; no request is sent, so a read costs no tokens and no quota.

  Claude: ~/.agent-hover/quota/claude.json (quota_tee.py in front of the status-line command),
          else the limit store of the coralline status line (~/.claude/coralline/limit-5h.d).
  Codex:  the last "rate_limits" record in the session file (written after each turn).
  agy:    ~/.agent-hover/quota/agy.json (quota_tee.py in front of agy's status-line command).

Each reader returns {"windows": [{"label": "5h", "used": percent, "resets_at": time or None}],
"ts": time of the data} or None. The data is as old as the agent's last turn or status line.
"""
import json
import re
import time
from pathlib import Path

from agent_hover import states

QUOTA_DIR = states.HOME / "quota"
STATUS_DIR = states.HOME / "status"  # <agent>-<session>.json: model, effort, context (quota_tee.py)
STATUS_KEEP_S = 7 * 86400
CORALLINE = Path.home() / ".claude" / "coralline"


def _load(agent):
    try:
        data = json.loads((QUOTA_DIR / f"{agent}.json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _label(minutes):
    if not isinstance(minutes, (int, float)) or minutes <= 0:
        return "?"
    if minutes % 1440 == 0:
        return f"{int(minutes // 1440)}d"
    return f"{int(minutes // 60)}h" if minutes % 60 == 0 else f"{int(minutes)}m"


def _coralline(span):
    """(used, resets_at, mtime) from the coralline store: one folder named <resets_at>_<used>."""
    best = None
    for p in (CORALLINE / f"limit-{span}.d").glob("*_*"):
        m = re.fullmatch(r"(\d{9,11})_(\d+(?:\.\d+)?)", p.name)
        if m:
            try:
                found = (float(m.group(2)), float(m.group(1)), p.stat().st_mtime)
            except OSError:
                continue
            best = found if best is None or found[2] > best[2] else best
    return best


def claude_quota(now=None):
    """A window that has reset already is left out (its use is no longer known), so an old
    claude.json (wrapper removed) does not hide the coralline store."""
    now = time.time() if now is None else now
    data = _load("claude")
    if data and isinstance(data.get("windows"), dict):
        labels = {"five_hour": "5h", "seven_day": "7d"}
        windows = [{"label": labels[k], "used": w["used"], "resets_at": w.get("resets_at")}
                   for k, w in data["windows"].items() if k in labels and isinstance(w, dict)
                   and isinstance(w.get("used"), (int, float)) and (w.get("resets_at") or now + 1) > now]
        if windows:
            return {"windows": windows, "ts": data.get("ts")}
    found = [(label, _coralline(span)) for label, span in (("5h", "5h"), ("7d", "7d"))]
    found = [(label, c) for label, c in found if c and c[1] > now]
    windows = [{"label": label, "used": c[0], "resets_at": c[1]} for label, c in found if c]
    return {"windows": windows, "ts": max(c[2] for _, c in found if c)} if windows else None


def codex_quota(text, mtime=None):
    """From the end of a Codex session file: the last rate_limits record with windows."""
    for line in reversed((text or "").splitlines()):
        if '"rate_limits"' not in line:
            continue
        try:
            rec = json.loads(line)
            limits = (rec.get("payload") or {}).get("rate_limits") or {}
        except (ValueError, AttributeError):
            continue
        windows = [{"label": _label(w.get("window_minutes")), "used": float(w["used_percent"]),
                    "resets_at": w.get("resets_at") if isinstance(w.get("resets_at"), (int, float)) else None}
                   for w in (limits.get("primary"), limits.get("secondary"))
                   if isinstance(w, dict) and isinstance(w.get("used_percent"), (int, float))]
        if windows:
            return {"windows": windows, "ts": mtime}
    return None


def codex_quota_file(path, tail_bytes=262144):
    try:
        mtime = Path(path).stat().st_mtime
    except (OSError, TypeError):
        return None
    return codex_quota(states._file_part(path, tail_bytes, tail=True), mtime)


def agy_quota(model=None):
    """The 5 h and weekly windows that apply to a model (the row's, else the status line's).
    A model with buckets of its own (gemini-argon-...-weekly) uses only those; any other model
    uses its family's (gemini-5h, gemini-weekly). Non-Gemini models are the "3p" family."""
    data = _load("agy")
    if not data or not isinstance(data.get("buckets"), dict):
        return None
    name = (model or data.get("model") or "").lower()
    if not name:
        return None
    family = "gemini" if name.startswith("gemini") else "3p"
    words = set(re.split(r"[^a-z0-9.]+", name))
    mine = []
    for key, b in data["buckets"].items():
        if key.startswith(family + "-") and isinstance(b, dict) and isinstance(b.get("remaining"), (int, float)):
            mine.append((key, key[len(family) + 1:].split("-")[0], b))
    own = [x for x in mine if x[1] not in ("5h", "weekly") and x[1] in words]
    use = own or [x for x in mine if x[1] in ("5h", "weekly")]
    windows = []
    for span, label in (("5h", "5h"), ("weekly", "7d")):
        fits = [b for key, _, b in use if key.endswith("-" + span)]
        if fits:
            b = min(fits, key=lambda b: b["remaining"])
            windows.append({"label": label, "used": round((1 - b["remaining"]) * 1000) / 10,
                            "resets_at": b.get("resets_at")})
    return {"windows": windows, "ts": data.get("ts")} if windows else None


def session_status(agent, session):
    """{model, effort, ctx, tok_in, tok_out, ts} of one Claude or agy session, from its last
    status-line input, or None."""
    if not session or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{7,63}", str(session)):
        return None
    try:
        data = json.loads((STATUS_DIR / f"{agent}-{session}.json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def codex_status(text, mtime=None):
    """{model, effort, ctx, tok_in, tok_out, ts} of a Codex session from the end of its file:
    the last turn_context (model, effort) and token_count (context window use)."""
    out = {"model": None, "effort": None, "ctx": None, "tok_in": None, "tok_out": None, "ts": mtime}
    for line in reversed((text or "").splitlines()):
        if out["model"] is None and '"turn_context"' in line:
            try:
                p = json.loads(line).get("payload") or {}
            except (ValueError, AttributeError):
                continue
            out["model"], out["effort"] = p.get("model"), p.get("effort")
        elif out["ctx"] is None and '"token_count"' in line and '"info":{' in line.replace(" ", ""):
            try:
                info = (json.loads(line).get("payload") or {}).get("info") or {}
            except (ValueError, AttributeError):
                continue
            last, total = info.get("last_token_usage") or {}, info.get("total_token_usage") or {}
            window = info.get("model_context_window")
            if isinstance(window, (int, float)) and window > 0 and isinstance(last.get("total_tokens"), (int, float)):
                out["ctx"] = round(last["total_tokens"] * 1000 / window) / 10
            out["tok_in"], out["tok_out"] = total.get("input_tokens"), total.get("output_tokens")
        if out["model"] is not None and out["ctx"] is not None:
            break
    return out if out["model"] or out["ctx"] is not None else None


def codex_status_file(path, tail_bytes=262144):
    try:
        mtime = Path(path).stat().st_mtime
    except (OSError, TypeError):
        return None
    return codex_status(states._file_part(path, tail_bytes, tail=True), mtime)


def prune_status(now):
    """Delete status files not written for STATUS_KEEP_S (one file per session)."""
    for p in STATUS_DIR.glob("*.json") if STATUS_DIR.is_dir() else []:
        try:
            if now - p.stat().st_mtime > STATUS_KEEP_S:
                p.unlink(missing_ok=True)
        except OSError:
            continue
