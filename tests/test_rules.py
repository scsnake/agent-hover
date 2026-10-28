from agent_hover.rules import Config, Tab, decide
from agent_hover.states import DONE, IDLE, WAITING, WORKING, iterm_session_uuid, map_event

CFG = Config(n_active=3, idle_park_s=600, working_park_s=30, settle_s=3)
NOW = 10_000.0


def tab(sid, state, age=100, **kw):
    kw.setdefault("window", "w1")
    return Tab(session=sid, state=state, state_ts=NOW - age, **kw)


def run(tabs, last_focus_change=NOW - 60, counts=None):
    return decide(tabs, NOW, CFG, last_focus_change, counts or {"w1": 99})


def test_working_tab_parks_after_it_loses_focus():
    d = run([tab("a", WORKING, last_focus_ts=NOW - 40), tab("b", WORKING, last_focus_ts=NOW - 10)])
    assert d.bury == ["a"]


def test_focused_tab_never_parks():
    d = run([tab("a", WORKING, age=5000, focused=True)])
    assert d.bury == []


def test_waiting_and_unseen_done_are_raised():
    d = run([tab("a", WAITING, buried=True), tab("b", DONE, buried=True, last_focus_ts=NOW - 500),
             tab("c", WORKING, buried=True)])
    assert d.unbury == ["a", "b"]


def test_seen_done_stays_until_idle_time():
    seen_recent = tab("a", DONE, age=700, last_focus_ts=NOW - 100)
    seen_long_ago = tab("b", DONE, age=900, last_focus_ts=NOW - 800)
    d = run([seen_recent, seen_long_ago])
    assert d.bury == ["b"]


def test_cap_parks_lowest_priority_first():
    tabs = [tab("w", WAITING), tab("i", IDLE, last_focus_ts=NOW - 5), tab("d", DONE, last_focus_ts=NOW - 5),
            tab("k", WORKING, last_focus_ts=NOW - 5)]
    d = run(tabs)
    assert d.bury == ["i"]  # 4 visible, cap 3: idle goes first


def test_cap_never_parks_waiting_or_focused():
    tabs = [tab(s, WAITING) for s in "abcd"] + [tab("f", WORKING, focused=True)]
    assert run(tabs).bury == []


def test_settle_time_blocks_actions():
    d = run([tab("a", WAITING, buried=True)], last_focus_change=NOW - 1)
    assert d.unbury == [] and d.bury == []


def test_never_empties_a_window():
    d = run([tab("a", WORKING, last_focus_ts=NOW - 40, window="w2")], counts={"w2": 1})
    assert d.bury == []


def test_pinned_tab_stays():
    d = run([tab("a", WORKING, last_focus_ts=NOW - 400, pinned=True)])
    assert d.bury == []


def test_claude_events():
    assert map_event("claude", {"hook_event_name": "UserPromptSubmit"})[0] == WORKING
    assert map_event("claude", {"hook_event_name": "PermissionRequest", "tool_name": "Bash"}) == (WAITING, "approve Bash")
    assert map_event("claude", {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion"})[0] == WAITING
    assert map_event("claude", {"hook_event_name": "Notification", "notification_type": "idle_prompt"}) is None
    assert map_event("claude", {"hook_event_name": "Notification", "notification_type": "permission_prompt"})[0] == WAITING
    assert map_event("claude", {"hook_event_name": "Stop"})[0] == DONE
    assert map_event("claude", {"hook_event_name": "SubagentStop"}) is None


def test_codex_events():
    assert map_event("codex", {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion"})[0] == WORKING
    assert map_event("codex", {"hook_event_name": "Interrupt"})[0] == IDLE
    assert map_event("codex", {"hook_event_name": "Notification"}) is None


def test_iterm_uuid():
    u = "243CB74F-A646-4BFE-A2AB-E462597C654F"
    assert iterm_session_uuid({"ITERM_SESSION_ID": f"w0t0p0:{u}"}) == u
    assert iterm_session_uuid({}) is None


def test_session_start_compact_keeps_state():
    assert map_event("codex", {"hook_event_name": "SessionStart", "source": "compact"}) is None
    assert map_event("claude", {"hook_event_name": "SessionStart", "source": "compact"}) is None
    assert map_event("codex", {"hook_event_name": "SessionStart", "source": "startup"})[0] == IDLE


def test_done_then_working_by_subagent_is_not_reburied_at_once():
    # Raised on "done", then a background subagent makes it "working" again 1 s later.
    # The person looked at it long ago, so only the state change can start the timer.
    raised = tab("a", DONE, age=1, buried=True, last_focus_ts=NOW - 900)
    assert run([raised]).unbury == ["a"]
    back = tab("a", WORKING, age=0, last_focus_ts=NOW - 900, state_since=NOW - 1)
    assert run([back]).bury == []
    later = tab("a", WORKING, age=0, last_focus_ts=NOW - 900, state_since=NOW - 31)
    assert run([later]).bury == ["a"]


def test_working_timer_still_starts_when_focus_left():
    t = tab("a", WORKING, age=0, last_focus_ts=NOW - 10, state_since=NOW - 600)
    assert run([t]).bury == []


def test_selected_tab_stays_while_iterm2_is_not_active():
    # The person works in this tab, then switches to another app: not focused (app inactive),
    # but still the selected tab of its window. It must not be parked, by time or by the cap.
    t = tab("busy", WORKING, age=0, last_focus_ts=NOW - 300, state_since=NOW - 300, selected=True)
    assert run([t]).bury == []
    many = [tab(s, IDLE, age=5000, last_focus_ts=NOW - 5000) for s in "abcd"] + [t]
    d = run(many)
    assert "busy" not in d.bury and len(d.bury) == 4


def test_selected_done_tab_is_not_seen_while_app_inactive():
    # Selected but iTerm2 inactive: "done" stays unseen, so it is still shown as new.
    t = tab("x", DONE, age=10, last_focus_ts=NOW - 300, selected=True)
    from agent_hover.rules import seen
    assert not seen(t)


def test_default_working_tab_parks_only_over_n():
    cfg = Config(n_active=2, idle_park_s=600, settle_s=3)  # working_park_s 0: no timer
    busy = [tab(s, WORKING, age=5000, last_focus_ts=NOW - 5000) for s in ("a", "b")]
    assert decide(busy, NOW, cfg, NOW - 60, {"w1": 99}).bury == []
    d = decide(busy + [tab("c", WORKING, age=5000, last_focus_ts=NOW - 100)], NOW, cfg, NOW - 60, {"w1": 99})
    assert d.bury == ["a"] and d.reasons["a"] == "park: over 2 active (working)"


def test_hidden_tab_stays_buried():
    # Hidden by hand: not raised although done and unseen, waiting (until the daemon ends the
    # hide on a state change), or the selected tab.
    d = run([tab("a", DONE, buried=True, hidden=True), tab("b", WAITING, buried=True, hidden=True),
             tab("c", IDLE, buried=True, hidden=True, selected=True)])
    assert d.unbury == []
