import json

from agent_hover import daemon, marks


def rec(agent_session, state="idle", agent="claude"):
    return {"agent": agent, "agent_session": agent_session, "state": state}


def test_old_pins_file_loads(tmp_path):
    (tmp_path / "pins.json").write_text(json.dumps(["T1", "box:tmux-1-2", 5]))
    assert set(marks.Marks(tmp_path / "pins.json")) == {"T1", "box:tmux-1-2"}


def test_pin_moves_to_the_restored_tab(tmp_path):
    path = tmp_path / "pins.json"
    pins = marks.Marks(path)
    pins.add("OLD")
    assert pins.sync({"OLD": rec("a1")}, {"OLD"}, 0) is True  # saves the agent session
    # iTerm2 restore: the tab has a new ID, the agent keeps its session. A new daemon reads the file.
    pins = marks.Marks(path)
    assert pins.sync({"NEW": rec("a1"), "X": rec("b2")}, {"NEW", "X"}, 1) is True
    assert set(pins) == {"NEW"} and set(marks.Marks(path)) == {"NEW"}


def test_mark_stays_on_its_tab_when_the_agent_session_changes(tmp_path):
    pins = marks.Marks(tmp_path / "pins.json")
    pins.add("T")
    pins.sync({"T": rec("a1")}, {"T"}, 0)
    pins.sync({"T": rec("a2"), "U": rec("a1")}, {"T", "U"}, 1)  # /clear in T; a1 resumed in U
    assert set(pins) == {"T"} and pins.items["T"]["agent_session"] == "a2"


def test_mark_of_a_gone_tab_waits_then_drops(tmp_path):
    pins = marks.Marks(tmp_path / "pins.json", gone_s=60)
    pins.add("T")
    pins.add("box:tmux-1-2")
    pins.sync({"T": rec("a1")}, {"T"}, 0)
    keep = daemon.remote.split_sid
    assert pins.sync({}, {"Y"}, 10, keep=keep) is False and "T" in pins  # not found yet: wait
    assert pins.sync({}, {"Y"}, 80, keep=keep) is True
    assert set(pins) == {"box:tmux-1-2"}  # a remote row is never dropped here


def test_hidden_mark_moves_and_keeps_a_rest_state(tmp_path):
    hidden = marks.Marks(tmp_path / "hidden.json")
    hidden["A"] = "done"
    hidden["B"] = "working"
    hidden.sync({"A": rec("a1"), "B": rec("b1", "working")}, {"A", "B"}, 0)
    hidden = marks.Marks(tmp_path / "hidden.json")
    # Restored tabs are seeded idle: A was done (at rest), so it stays hidden; B finished a turn.
    hidden.sync({"A2": rec("a1"), "B2": rec("b1")}, {"A2", "B2"}, 1, moved=daemon.rest_state_moved)
    assert hidden.as_dict() == {"A2": "idle", "B2": "working"}


def test_mark_does_not_move_onto_a_marked_tab(tmp_path):
    pins = marks.Marks(tmp_path / "pins.json", gone_s=60)
    pins.add("OLD")
    pins.add("NEW")
    pins.sync({"OLD": rec("a1"), "NEW": rec("b1")}, {"OLD", "NEW"}, 0)
    pins.sync({"NEW": rec("a1")}, {"NEW"}, 1)
    assert set(pins) == {"OLD", "NEW"}
    pins.sync({"NEW": rec("a1")}, {"NEW"}, 100)
    assert set(pins) == {"NEW"}


class _Session:
    def __init__(self, sid):
        self.session_id, self.buried = sid, False

    async def async_set_buried(self, value):
        self.buried = value


class _Tab:
    def __init__(self, sid):
        self.tab_id, self.all_sessions = sid, [_Session(sid)]


class _Window:
    window_id = "W"

    def __init__(self, *sids):
        self.tabs = [_Tab(s) for s in sids]
        self.current_tab = self.tabs[0]


class _App:
    def __init__(self, window):
        self.terminal_windows = [window]

    async def async_refresh(self):
        pass

    def get_session_by_id(self, sid):
        return next((t.all_sessions[0] for t in self.terminal_windows[0].tabs if t.tab_id == sid), None)


class _Focus:
    def __init__(self, sid=None):
        self.sid = sid

    async def refresh(self, app):
        pass

    def has(self, sid):
        return sid == self.sid


def test_bury_a_selected_hidden_tab_but_not_the_focused_one(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setattr(daemon.states, "BURIED_FILE", tmp_path / "buried.json")
    # A restored hidden tab that is the selected tab of a background window.
    app = _App(_Window("SEL", "OTHER"))
    assert asyncio.run(daemon.bury(app, "SEL", _Focus(), {}, "park")) is False  # normal parking: never
    assert asyncio.run(daemon.bury(app, "SEL", _Focus(), {}, "park", selected_ok=True)) is True
    app = _App(_Window("SEL", "OTHER"))
    assert asyncio.run(daemon.bury(app, "SEL", _Focus("SEL"), {}, "park", selected_ok=True)) is False
