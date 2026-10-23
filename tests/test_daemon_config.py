import argparse

from agent_hover import agents, daemon


def args(**kw):
    base = {"n": None, "idle_min": None, "working_park_s": None, "cpu_hot_pct": None, "cpu_hot_min": None}
    base.update(kw)
    return argparse.Namespace(**base)


def test_defaults_without_config():
    a = args()
    daemon.apply_config(a, {})
    assert (a.n, a.idle_min, a.working_park_s) == (5, 20.0, 0.0)


def test_config_then_flag_wins(monkeypatch):
    monkeypatch.setattr(agents, "AGENTS", ["claude", "codex"])
    a = args(n=3)
    daemon.apply_config(a, {"daemon": {"n_active": 8, "idle_min": 5, "working_park_s": "bad"},
                            "agents": {"extra": ["qwen", "claude", 7]}})
    assert (a.n, a.idle_min, a.working_park_s) == (3, 5, 0.0)
    assert agents.AGENTS == ["claude", "codex", "qwen"]
    assert daemon.detect_agent("qwen", "") == "qwen"


def test_cpu_hot_defaults():
    a = args()
    daemon.apply_config(a, {"daemon": {"cpu_hot_pct": 80}})
    assert (a.cpu_hot_pct, a.cpu_hot_min) == (80, 2.0)
