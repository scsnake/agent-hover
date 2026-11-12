"""run_watched: cancel a run() that hangs on a dead iTerm2 link, and report LOST."""
import asyncio
import signal
import types

from agent_hover import daemon


class Conn:
    def __init__(self):
        self.websocket = types.SimpleNamespace(closed=False)


def test_hang_on_closed_link_is_lost(monkeypatch):
    monkeypatch.setattr(daemon, "WATCH_S", 0.01)
    cancelled = []

    async def fake_run(conn, args):
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, lambda: None)
        try:
            conn.websocket.closed = True  # iTerm2 quit ...
            await asyncio.Event().wait()  # ... and the pending API call never returns
        finally:
            cancelled.append(True)

    monkeypatch.setattr(daemon, "run", fake_run)
    assert asyncio.run(daemon.run_watched(Conn(), None)) == daemon.LOST
    assert cancelled == [True]  # its finally ran: the remote threads stop


def test_error_on_closed_link_is_lost(monkeypatch):
    monkeypatch.setattr(daemon, "WATCH_S", 0.01)
    monkeypatch.setattr(daemon, "log", lambda *a, **k: None)

    async def fake_run(conn, args):
        conn.websocket = None
        raise ConnectionError("gone")

    monkeypatch.setattr(daemon, "run", fake_run)
    assert asyncio.run(daemon.run_watched(Conn(), None)) == daemon.LOST


def test_normal_stop_returns_result(monkeypatch):
    monkeypatch.setattr(daemon, "WATCH_S", 0.01)

    async def fake_run(conn, args):
        await asyncio.sleep(0.03)
        return "done"

    monkeypatch.setattr(daemon, "run", fake_run)
    assert asyncio.run(daemon.run_watched(Conn(), None)) == "done"
