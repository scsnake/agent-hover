import asyncio

from agent_hover import daemon

SID = "65684c64-134b-42bc-814e-74fef75929bf"


class FakeSession:
    session_id = "NEW"

    def __init__(self):
        self.typed = []
        self.activated = False

    async def async_send_text(self, text):
        self.typed.append(text)

    async def async_activate(self, select_tab=True, order_window_front=True):
        self.activated = True


class FakeTab:
    def __init__(self):
        self.current_session = FakeSession()


class FakeWindow:
    def __init__(self):
        self.tabs = []

    async def async_create_tab(self):
        self.tabs.append(FakeTab())
        return self.tabs[-1]


class FakeApp:
    def __init__(self):
        self.current_terminal_window = FakeWindow()

    async def async_activate(self, raise_all_windows=True, ignoring_other_apps=False):
        pass


def run(req, monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(daemon.asyncio, "sleep", lambda s: real_sleep(0))
    app = FakeApp()
    asyncio.run(daemon.open_resume_tab(app, req))
    return app.current_terminal_window.tabs


def test_resume_runs_cd_and_types_the_command(monkeypatch, tmp_path):
    folder = tmp_path / "my app"
    folder.mkdir()
    tabs = run({"action": "resume", "agent": "claude", "agent_session": SID, "cwd": str(folder)}, monkeypatch)
    s = tabs[0].current_session
    assert s.typed == [f"cd '{folder}'\n", f"claude --resume {SID}"]  # no Enter after the command
    assert s.activated


def test_resume_refuses_bad_requests(monkeypatch, tmp_path):
    for req in [{"agent": "bash", "agent_session": SID, "cwd": str(tmp_path)},
                {"agent": "codex", "agent_session": "x; rm -rf ~", "cwd": str(tmp_path)},
                {"agent": "claude", "agent_session": SID, "cwd": str(tmp_path / "missing")}]:
        assert run({"action": "resume", **req}, monkeypatch) == []
