"""Which agent tabs to bury (park) and which to unbury (raise). Pure: no iTerm2 calls."""
from dataclasses import dataclass, field

from agent_hover.states import DONE, IDLE, WAITING, WORKING


@dataclass
class Config:
    n_active: int = 5            # max visible agent tabs, when the rules can keep to it
    idle_park_s: float = 20 * 60  # park a seen "done" or "idle" tab after this time
    working_park_s: float = 120   # park a working tab this long after it lost focus
    settle_s: float = 3           # do nothing this soon after a focus change


@dataclass
class Tab:
    session: str
    window: str
    state: str
    state_ts: float              # time of the last hook event (every tool call updates it)
    buried: bool = False
    focused: bool = False
    last_focus_ts: float = 0.0   # last time this session had focus (0 = never seen by the daemon)
    pinned: bool = False         # the person unburied it by hand; keep it until its state changes
    state_since: float = 0.0     # when the current state began (0 = unknown: use state_ts)
    selected: bool = False       # the selected tab of its window, also while iTerm2 is not active


@dataclass
class Decision:
    bury: list = field(default_factory=list)
    unbury: list = field(default_factory=list)
    reasons: dict = field(default_factory=dict)


def seen(t):
    """The person looked at the tab after its current state began."""
    return t.focused or t.last_focus_ts >= t.state_ts


def must_show(t):
    # A window's selected tab stays: the person comes back to it after using another app.
    return (t.focused or t.selected or t.pinned or t.state == WAITING
            or (t.state == DONE and not seen(t)))


def parkable(t, now, cfg):
    if must_show(t):
        return False
    # Time from the later of "the person left the tab" and "the state began". So a tab that
    # just came back (done) and went to working again (a background subagent) gets a new
    # working_park_s, and is not buried again at once.
    since = now - max(t.last_focus_ts, t.state_since or t.state_ts)
    if t.state == WORKING:
        return since > cfg.working_park_s
    if t.state in (DONE, IDLE):
        return since > cfg.idle_park_s
    return False


# Lower value = parked first when the visible tabs are more than n_active.
PARK_ORDER = {IDLE: 0, DONE: 1, WORKING: 2}


def decide(tabs, now, cfg, last_focus_change_ts, window_tab_counts):
    """window_tab_counts: visible tabs per window, agent tabs or not (a window must keep one)."""
    d = Decision()
    if now - last_focus_change_ts < cfg.settle_s:
        return d
    for t in tabs:
        if t.buried and must_show(t):
            d.unbury.append(t.session)
            d.reasons[t.session] = f"raise: {t.state}"
    visible = [t for t in tabs if not t.buried]
    for t in visible:
        if parkable(t, now, cfg):
            d.bury.append(t.session)
            d.reasons[t.session] = f"park: {t.state}"
    remaining = [t for t in visible if t.session not in d.bury]
    over = len(remaining) + len(d.unbury) - cfg.n_active
    if over > 0:
        extra = sorted((t for t in remaining if not must_show(t)),
                       key=lambda t: (PARK_ORDER.get(t.state, 0), max(t.last_focus_ts, t.state_ts)))
        for t in extra[:over]:
            d.bury.append(t.session)
            d.reasons[t.session] = f"park: over {cfg.n_active} active ({t.state})"
    # Never empty a window: iTerm2 closes a window that has no tabs.
    left = dict(window_tab_counts)
    keep = []
    for sid in d.bury:
        win = next(t.window for t in tabs if t.session == sid)
        if left.get(win, 0) > 1:
            left[win] -= 1
            keep.append(sid)
        else:
            d.reasons.pop(sid, None)
    d.bury = keep
    return d
