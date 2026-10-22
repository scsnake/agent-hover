"""Point AGENT_HOVER_HOME at a temp folder before any test imports agent_hover.

The paths in agent_hover.states (state, log, buried.json) are set at import time, so a test
that forgets to patch one of them would otherwise write to the real ~/.agent-hover
(the daemon's log() did: fake "raise_request" lines for session "S" in the real log).
"""
import os
import tempfile

os.environ["AGENT_HOVER_HOME"] = tempfile.mkdtemp(prefix="agent-hover-test-")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def quiet_keyboard(monkeypatch):
    """The daemon waits while the person types; tests must not depend on the real keyboard."""
    from agent_hover import daemon
    monkeypatch.setattr(daemon, "keyboard_idle_s", lambda: 1e9)
