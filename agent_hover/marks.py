"""Marks on agent tabs (pins, hides) that outlive the iTerm2 tab ID.

After an iTerm2 crash or upgrade, iTerm2 gives each restored tab a new session ID, but the agent
in it keeps its own session ID. So a mark is saved with both: the tab ID finds the tab while it
lives, the agent session ID finds it again after a restore. Remote rows ("host:tmux-...") have
no agent session here and keep the tab ID only.

The file is a JSON list of {"tab", "agent"?, "agent_session"?, "value"?}. An older pins.json
(a list of tab IDs) still loads.
"""
import json

from agent_hover import states

GONE_S = 120.0  # a mark whose tab is gone this long is dropped (a restore gets new tabs sooner)


class Marks:
    """Tab ID -> value (None for a pin, the state at hide time for a hide). Every change is saved."""

    def __init__(self, path, gone_s=GONE_S):
        self.path, self.gone_s = path, gone_s
        self.gone = {}  # tab ID -> time it was first seen gone (in memory: a restart waits again)
        self.items = {}
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = []
        for e in data if isinstance(data, list) else []:
            e = {"tab": e} if isinstance(e, str) else e
            if isinstance(e, dict) and isinstance(e.get("tab"), str) and e["tab"]:
                self.items[e["tab"]] = {k: e[k] for k in ("tab", "agent", "agent_session", "value") if k in e}

    def save(self):
        states.HOME.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(self.items.values(), key=lambda e: e["tab"]), ensure_ascii=False))
        tmp.replace(self.path)

    # A mapping (hides) and a set (pins) of tab IDs.
    def __contains__(self, sid):
        return sid in self.items

    def __iter__(self):
        return iter(list(self.items))

    def __len__(self):
        return len(self.items)

    def __getitem__(self, sid):
        return self.items[sid].get("value")

    def __setitem__(self, sid, value):
        self.items[sid] = {**self.items.get(sid, {}), "tab": sid, "value": value}
        self.save()

    def add(self, sid):
        if sid not in self.items:
            self.items[sid] = {"tab": sid}
            self.save()

    def pop(self, sid, default=None):
        if sid not in self.items:
            return default
        value = self.items.pop(sid).get("value")
        self.gone.pop(sid, None)
        self.save()
        return value

    def discard(self, sid):
        self.pop(sid)

    def as_dict(self):
        return {sid: e.get("value") for sid, e in self.items.items()}

    def sync(self, recs, live, now, keep=lambda sid: False, moved=None):
        """Point each mark at its tab and save the agent session that runs in it.

        recs: tab ID -> state record. live: tab IDs that iTerm2 shows or keeps buried now.
        A mark stays on its tab while the tab lives (the agent session there may change, for
        example with /clear). If the tab is gone, the live tab whose agent runs the saved agent
        session takes the mark, and moved(entry, record) may update its value. A mark with
        no tab for gone_s is dropped; keep(tab ID) true: never dropped (remote rows).
        Returns True if anything changed."""
        by_session = {}
        for sid in sorted(live):
            r = recs.get(sid) or {}
            if r.get("agent_session") and r.get("state") != states.ENDED:
                by_session.setdefault((r.get("agent"), r["agent_session"]), sid)
        changed = False
        for sid, e in list(self.items.items()):
            if sid in live:
                self.gone.pop(sid, None)
                r = recs.get(sid) or {}
                if r.get("agent_session") and (r.get("agent"), r["agent_session"]) != (e.get("agent"), e.get("agent_session")):
                    e.update(agent=r.get("agent"), agent_session=r["agent_session"])
                    changed = True
                continue
            if keep(sid):
                continue
            new = by_session.get((e.get("agent"), e.get("agent_session"))) if e.get("agent_session") else None
            if new is not None and new not in self.items:
                del self.items[sid]
                self.gone.pop(sid, None)
                e["tab"] = new
                if moved is not None:
                    moved(e, recs.get(new) or {})
                self.items[new] = e
                changed = True
            elif now - self.gone.setdefault(sid, now) > self.gone_s:
                del self.items[sid]
                self.gone.pop(sid)
                changed = True
        if changed:
            self.save()
        return changed
