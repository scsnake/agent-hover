import json
import os
import subprocess
import sys
from pathlib import Path

from agent_hover import quota

TEE = Path(__file__).resolve().parent.parent / "agent_hover" / "quota_tee.py"


def run_tee(tmp_path, agent, payload, *command):
    env = {**os.environ, "AGENT_HOVER_HOME": str(tmp_path)}
    args = [sys.executable, "-I", str(TEE), agent] + (["--", *command] if command else [])
    return subprocess.run(args, input=json.dumps(payload).encode(), capture_output=True, env=env)


def test_tee_saves_claude_limits_and_runs_the_command(tmp_path, monkeypatch):
    payload = {"model": {"id": "claude-opus-5-5"},
               "rate_limits": {"five_hour": {"used_percentage": 16, "resets_at": 1793506800},
                               "seven_day": {"used_percentage": 35.5, "resets_at": 1793703600}}}
    r = run_tee(tmp_path, "claude", payload, "sh", "-c", "cat | wc -c")
    assert r.returncode == 0 and int(r.stdout) == len(json.dumps(payload))  # same input, its output only
    monkeypatch.setattr(quota, "QUOTA_DIR", tmp_path / "quota")
    q = quota.claude_quota(now=0)
    assert q["windows"] == [{"label": "5h", "used": 16.0, "resets_at": 1793506800},
                            {"label": "7d", "used": 35.5, "resets_at": 1793703600}]


def test_tee_never_fails_the_status_line(tmp_path):
    env = {**os.environ, "AGENT_HOVER_HOME": str(tmp_path)}
    r = subprocess.run([sys.executable, "-I", str(TEE), "claude", "--", "echo", "bar"], input=b"not json",
                       capture_output=True, env=env)
    assert r.returncode == 0 and r.stdout == b"bar\n" and not (tmp_path / "quota").exists()


def test_agy_buckets_per_model(tmp_path, monkeypatch):
    payload = {"model": {"id": "gemini-3.8-flash-high"}, "quota": {
        "gemini-5h": {"remaining_fraction": 0.75, "reset_in_seconds": 600},
        "gemini-weekly": {"remaining_fraction": 0.5, "reset_in_seconds": 86400},
        "gemini-argon-wtu-weekly": {"remaining_fraction": 0.1, "reset_in_seconds": 7200},
        "3p-5h": {"remaining_fraction": 1.0, "reset_in_seconds": 0}}}
    assert run_tee(tmp_path, "agy", payload).returncode == 0
    monkeypatch.setattr(quota, "QUOTA_DIR", tmp_path / "quota")
    flash = quota.agy_quota()
    assert [(w["label"], w["used"]) for w in flash["windows"]] == [("5h", 25.0), ("7d", 50.0)]
    argon = quota.agy_quota("Gemini 4 Argon (High)")  # its own bucket only
    assert [(w["label"], w["used"]) for w in argon["windows"]] == [("7d", 90.0)]
    assert [(w["label"], w["used"]) for w in quota.agy_quota("claude-opus")["windows"]] == [("5h", 0.0)]


def test_codex_windows_from_session_tail():
    rec = {"type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
        "primary": {"used_percent": 16.0, "window_minutes": 300, "resets_at": 1793513405},
        "secondary": {"used_percent": 34.0, "window_minutes": 10080, "resets_at": 1793777299}}}}
    q = quota.codex_quota("cut line\n" + json.dumps(rec) + "\n{}", mtime=5.0)
    assert q == {"windows": [{"label": "5h", "used": 16.0, "resets_at": 1793513405},
                             {"label": "7d", "used": 34.0, "resets_at": 1793777299}], "ts": 5.0}
    assert quota.codex_quota("{}") is None


def test_claude_from_coralline_store(tmp_path, monkeypatch):
    monkeypatch.setattr(quota, "QUOTA_DIR", tmp_path / "none")
    monkeypatch.setattr(quota, "CORALLINE", tmp_path)
    (tmp_path / "limit-5h.d" / "1793506800_016.000").mkdir(parents=True)
    (tmp_path / "limit-7d.d" / "1793703600_035.000").mkdir(parents=True)
    q = quota.claude_quota(now=0)
    assert [(w["label"], w["used"], w["resets_at"]) for w in q["windows"]] == [
        ("5h", 16.0, 1793506800.0), ("7d", 35.0, 1793703600.0)]


def test_tee_saves_session_status(tmp_path, monkeypatch):
    sid = "d80d1af4-96fd-42bc-9aca-bf1de4130e11"
    payload = {"session_id": sid, "model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
               "effort": {"level": "high"},
               "context_window": {"used_percentage": 31, "total_input_tokens": 310700, "total_output_tokens": 330}}
    assert run_tee(tmp_path, "claude", payload).returncode == 0
    monkeypatch.setattr(quota, "STATUS_DIR", tmp_path / "status")
    st = quota.session_status("claude", sid)
    assert (st["model"], st["effort"], st["ctx"], st["tok_in"], st["tok_out"]) == ("Opus 5.5", "high", 31.0, 310700.0, 330.0)
    assert quota.session_status("claude", "../../etc/passwd") is None
    bad = run_tee(tmp_path, "claude", {**payload, "session_id": "../x"})
    assert bad.returncode == 0 and sorted(p.name for p in (tmp_path / "status").iterdir()) == [f"claude-{sid}.json"]


def test_codex_status_from_session_tail():
    ctx = {"type": "turn_context", "payload": {"model": "gpt-5.6-sol", "effort": "high"}}
    tok = {"type": "event_msg", "payload": {"type": "token_count", "info": {
        "total_token_usage": {"input_tokens": 1655140, "output_tokens": 18652},
        "last_token_usage": {"total_tokens": 124146}, "model_context_window": 258400}}}
    st = quota.codex_status("\n".join(json.dumps(x) for x in (ctx, tok)), mtime=7.0)
    assert (st["model"], st["effort"], st["ctx"], st["tok_in"], st["tok_out"], st["ts"]) == (
        "gpt-5.6-sol", "high", 48.0, 1655140, 18652, 7.0)


def test_idle_claude_session_does_not_replace_newer_limits(tmp_path, monkeypatch):
    import time

    now = time.time()
    monkeypatch.setattr(quota, "QUOTA_DIR", tmp_path / "quota")

    def send(used5, reset5, used7=10, reset7=None):
        payload = {"rate_limits": {"five_hour": {"used_percentage": used5, "resets_at": reset5},
                                   "seven_day": {"used_percentage": used7, "resets_at": reset7 or now + 86400}}}
        assert run_tee(tmp_path, "claude", payload).returncode == 0
        return {w["label"]: w["used"] for w in quota.claude_quota()["windows"]}

    assert send(20, now + 3000)["5h"] == 20
    assert send(12, now + 3000)["5h"] == 20       # an idle session, same window, older reading
    assert send(30, now + 3000)["5h"] == 30       # use grows
    assert send(50, now - 100)["5h"] == 30        # a window that has reset already
    assert send(5, now + 3000 + 18000)["5h"] == 5  # a new window
    assert send(40, now + 3000)["5h"] == 5        # an old window from an idle session


def test_reset_claude_windows_fall_back_to_coralline(tmp_path, monkeypatch):
    T = 1793500000
    monkeypatch.setattr(quota, "QUOTA_DIR", tmp_path / "quota")
    monkeypatch.setattr(quota, "CORALLINE", tmp_path / "coralline")
    (tmp_path / "quota").mkdir()
    (tmp_path / "quota" / "claude.json").write_text(json.dumps({"windows": {
        "five_hour": {"used": 80, "resets_at": T + 100}, "seven_day": {"used": 40, "resets_at": T + 900}}, "ts": T}))
    assert [w["label"] for w in quota.claude_quota(now=T + 500)["windows"]] == ["7d"]  # 5h has reset
    (tmp_path / "coralline" / "limit-5h.d" / f"{T + 2000}_012.000").mkdir(parents=True)
    q = quota.claude_quota(now=T + 1000)  # both windows of claude.json have reset
    assert [(w["label"], w["used"]) for w in q["windows"]] == [("5h", 12.0)]
    assert quota.claude_quota(now=T + 3000) is None


def test_tee_ignores_true_false_numbers(tmp_path, monkeypatch):
    payload = {"rate_limits": {"five_hour": {"used_percentage": True, "resets_at": 1793506800},
                               "seven_day": {"used_percentage": 35, "resets_at": False}}}
    assert run_tee(tmp_path, "claude", payload).returncode == 0
    monkeypatch.setattr(quota, "QUOTA_DIR", tmp_path / "quota")
    assert quota.claude_quota(now=0)["windows"] == [{"label": "7d", "used": 35.0, "resets_at": None}]
