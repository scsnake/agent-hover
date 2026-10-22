import json

from agent_hover import daemon


def test_clean_name_drops_status_glyphs():
    assert daemon.clean_name("✳ Docs site") == "Docs site"
    assert daemon.clean_name("◑ Agent-board fork vs rewrite") == "Agent-board fork vs rewrite"
    assert daemon.clean_name("⠂ 測試專案 feasibility") == "測試專案 feasibility"
    assert daemon.clean_name("(ssh) host") == "(ssh) host"
    assert daemon.clean_name(None) == ""


def test_panel_rows_order_buried_last(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "PANEL_FILE", tmp_path / "panel.json")
    monkeypatch.setattr(daemon.states, "HOME", tmp_path)
    rows = [{"session": s, "state": st, "since": t, "buried": b}
            for s, st, t, b in [("b1", "idle", 1, True), ("w", "working", 5, False),
                                ("q", "waiting", 3, False), ("d", "done", 4, False), ("b2", "waiting", 2, True)]]
    daemon.write_panel(rows, apply=True)
    order = [r["session"] for r in json.loads((tmp_path / "panel.json").read_text())["rows"]]
    assert order == ["q", "d", "w", "b2", "b1"]


class FakeSession:
    def __init__(self, sid):
        self.session_id = sid
        self.activated = False

    async def async_activate(self, select_tab=True, order_window_front=True):
        self.activated = True


class FakeApp:
    def __init__(self, sid):
        self.session = FakeSession(sid)
        self.buried_sessions = [self.session]
        self.app_activated = False

    async def async_refresh(self):
        pass

    def get_session_by_id(self, sid):
        return self.session if sid == self.session.session_id else None

    async def async_activate(self, raise_all_windows=True, ignoring_other_apps=False):
        self.app_activated = True


def test_panel_click_on_idle_tab_is_a_focus_not_a_pin(tmp_path, monkeypatch):
    import asyncio
    import time

    from agent_hover.rules import Config, Tab, decide

    monkeypatch.setattr(daemon.states, "HOME", tmp_path)
    monkeypatch.setattr(daemon, "REQUEST_DIR", tmp_path / "requests")
    monkeypatch.setattr(daemon.states, "BURIED_FILE", tmp_path / "buried.json")
    unburied = []

    async def fake_unbury(app, sid, info, keep_selection=True):
        unburied.append((sid, keep_selection))
        app.buried_sessions = []
        return True

    monkeypatch.setattr(daemon, "unbury", fake_unbury)
    (tmp_path / "requests").mkdir()
    (tmp_path / "requests" / "r.json").write_text(json.dumps({"action": "raise", "session": "S", "ts": time.time()}))
    app, ours, last_focus = FakeApp("S"), {"S": {"window": "w1", "index": 0}}, {}
    asyncio.run(daemon.handle_requests(app, ours, last_focus))

    assert unburied == [("S", False)] and app.session.activated and app.app_activated
    assert "S" not in ours and not list((tmp_path / "requests").glob("*.json"))
    clicked = last_focus["S"]
    assert time.time() - clicked < 5

    cfg = Config(n_active=5, idle_park_s=1200, working_park_s=30, settle_s=3)
    idle = dict(session="S", window="w1", state="idle", state_ts=clicked - 5000, state_since=clicked - 5000,
                last_focus_ts=clicked)  # no pin: the click is only a focus
    soon = decide([Tab(**idle)], clicked + 60, cfg, clicked - 60, {"w1": 9})
    assert soon.bury == []                     # just looked at: stays
    later = decide([Tab(**idle)], clicked + 1201, cfg, clicked - 60, {"w1": 9})
    assert later.bury == ["S"]                 # 20 min without focus: buried again


def test_detect_agent():
    assert daemon.detect_agent("claude", "claude --allow-dangerously-skip-permissions -") == "claude"
    assert daemon.detect_agent("2.1.305", "claude -") == "claude"          # jobName-like title
    assert daemon.detect_agent("node", "node /opt/homebrew/bin/codex") == "codex"
    assert daemon.detect_agent("codex", None) == "codex"
    assert daemon.detect_agent("ssh", "ssh devbox") is None
    assert daemon.detect_agent("-zsh", "-zsh") is None
    assert daemon.detect_agent(None, None) is None


def test_title_state_from_glyph():
    assert daemon.title_state("✳ Docs site") == "idle"
    assert daemon.title_state("◑ Agent-board fork vs rewrite") == "working"
    assert daemon.title_state("⠂ 測試專案") == "working"
    assert daemon.title_state("Plain title") == "idle"
    assert daemon.title_state(None) == "idle"


def test_seed_record():
    r = daemon.seed_record("S", "claude", "✳ Docs site", "/tmp/x", 100.0)
    assert (r["state"], r["agent"], r["event"], r["ts"], r["cwd"]) == ("idle", "claude", "seed", 100.0, "/tmp/x")


def test_restore_is_newest_first(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setattr(daemon.states, "HOME", tmp_path)
    monkeypatch.setattr(daemon.states, "BURIED_FILE", tmp_path / "buried.json")
    daemon.save_buried({"first": {"window": "w", "index": 3, "ts": 1.0},
                        "second": {"window": "w", "index": 3, "ts": 2.0},
                        "third": {"window": "w", "index": 0, "ts": 3.0}})
    order = []

    async def fake_unbury(app, sid, info, keep_selection=True):
        order.append(sid)
        return True

    monkeypatch.setattr(daemon, "unbury", fake_unbury)
    asyncio.run(daemon.restore_all(app=None))
    assert order == ["third", "second", "first"]
    assert daemon.load_buried() == {}


def test_pin_and_unpin_requests(tmp_path, monkeypatch):
    import asyncio

    from agent_hover.rules import Config, Tab, decide

    monkeypatch.setattr(daemon, "REQUEST_DIR", tmp_path / "requests")
    monkeypatch.setattr(daemon, "PINS_FILE", tmp_path / "pins.json")
    (tmp_path / "requests").mkdir()
    (tmp_path / "requests" / "a.json").write_text(json.dumps({"action": "pin", "session": "S"}))
    asyncio.run(daemon.handle_requests(FakeApp("S"), {}, {}))
    assert daemon.load_pins() == {"S"}
    # A pinned idle tab is never parked, also far past idle_park_s and over the cap.
    cfg = Config(n_active=1, idle_park_s=60, working_park_s=30, settle_s=3)
    tabs = [Tab(session=s, window="w1", state="idle", state_ts=0, state_since=0, pinned=(s == "S"))
            for s in ("S", "T", "U")]
    d = decide(tabs, 10_000, cfg, 0, {"w1": 9})
    assert "S" not in d.bury and {"T", "U"} <= set(d.bury)
    (tmp_path / "requests" / "b.json").write_text(json.dumps({"action": "unpin", "session": "S"}))
    asyncio.run(daemon.handle_requests(FakeApp("S"), {}, {}))
    assert daemon.load_pins() == set()
