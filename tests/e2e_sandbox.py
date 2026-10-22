"""End-to-end check in a sandbox: a throwaway iTerm2 window with 4 fake agent tabs.

  uv run python tests/e2e_sandbox.py <empty sandbox dir>

The daemon runs with AGENT_HOVER_HOME=<sandbox dir>, so it sees state files only for
the test tabs and cannot touch real tabs. The test window closes at the end.
"""
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import iterm2

HOME = sys.argv[1]
PROJ = str(Path(__file__).resolve().parent.parent)
ENV = dict(os.environ, AGENT_HOVER_HOME=HOME)


def hook(uuid, payload, agent="claude"):
    subprocess.run([sys.executable, "-I", f"{PROJ}/agent_hover/hook.py", agent],
                   input=json.dumps(payload).encode(),
                   env=dict(ENV, ITERM_SESSION_ID=f"w9t9p0:{uuid}"), check=True)


async def snapshot(app, wid):
    await app.async_refresh()
    win = app.get_window_by_id(wid)
    return ([t.current_session.session_id[:4] for t in win.tabs] if win else None,
            [s.session_id[:4] for s in app.buried_sessions])


def outside(app, wid):
    """Tab order and buried sessions outside the test window: must not change."""
    return ([[t.current_session.session_id for t in x.tabs] for x in app.terminal_windows if x.window_id != wid],
            sorted(s.session_id for s in app.buried_sessions))


async def main(conn):
    app = await iterm2.async_get_app(conn)
    await app.async_refresh()
    before = outside(app, None)
    w = await iterm2.Window.async_create(conn, command="/bin/sleep 900")
    for _ in range(3):
        await w.async_create_tab(command="/bin/sleep 900")
    await asyncio.sleep(1)
    await app.async_refresh()
    win = app.get_window_by_id(w.window_id)
    a, b, c, d = [t.current_session.session_id for t in win.tabs]
    await win.tabs[1].async_select()
    print("A B C D =", [x[:4] for x in (a, b, c, d)], "; focused B")
    hook(a, {"hook_event_name": "UserPromptSubmit"})
    hook(b, {"hook_event_name": "Stop"})
    hook(c, {"hook_event_name": "PermissionRequest", "tool_name": "Bash"}, agent="codex")
    hook(d, {"hook_event_name": "PreToolUse", "tool_name": "Bash"}, agent="codex")
    daemon = subprocess.Popen(["uv", "run", "python", "-m", "agent_hover.daemon", "--apply", "--n", "3",
                               "--working-park-s", "4", "--idle-min", "60", "--only-window", w.window_id],
                              cwd=PROJ, env=ENV,
                              stdout=open(f"{HOME}/daemon.out", "w"), stderr=subprocess.STDOUT)
    try:
        await asyncio.sleep(9)
        print("t+9s tabs, buried:", await snapshot(app, w.window_id), "(expect A and D buried)")
        t0 = time.time()
        hook(a, {"hook_event_name": "Stop"})
        for _ in range(40):
            await asyncio.sleep(0.25)
            tabs, bur = await snapshot(app, w.window_id)
            if a[:4] in tabs:
                break
        back_s = time.time() - t0
        await asyncio.sleep(0.5)  # unbury = new tab first, then the move back and the reselect
        tabs, bur = await snapshot(app, w.window_id)
        print(f"A done -> back after {back_s:.1f}s; tabs, buried:", (tabs, bur),
              "(expect A back at index 0)")
        cur = app.get_window_by_id(w.window_id).current_tab.current_session.session_id[:4]
        print("focused after unbury:", cur, "(expect B", b[:4] + ")")
        # Flap check: A is seen, left for longer than working_park_s, then goes working again
        # (like a background subagent). It must get a fresh timer, not an immediate re-bury.
        await app.get_tab_by_id(app.get_session_by_id(a).tab.tab_id).async_activate(order_window_front=False)
        await asyncio.sleep(2)
        await app.get_tab_by_id(app.get_session_by_id(b).tab.tab_id).async_activate(order_window_front=False)
        await asyncio.sleep(6)
        hook(a, {"hook_event_name": "PreToolUse", "tool_name": "Bash"})
        await asyncio.sleep(2)
        tabs, bur = await snapshot(app, w.window_id)
        print("A working again, +2s: A visible?", a[:4] in tabs, "(expect True)")
        await asyncio.sleep(5)
        tabs, bur = await snapshot(app, w.window_id)
        print("A working again, +7s: A buried?", a[:4] in bur, "(expect True)")
        shot = os.environ.get("PANEL_SCREENSHOT")
        if shot:  # the hover panel on the sandbox data, list kept open
            panel = subprocess.Popen([f"{PROJ}/.build/agent-hover-panel"],
                                     env=dict(ENV, AGENT_HOVER_PANEL_EXPANDED="1"))
            await asyncio.sleep(2.5)
            subprocess.run(["screencapture", "-x", "-R", os.environ.get("PANEL_REGION", "0,0,800,300"), shot])
            panel.terminate()
            print("panel screenshot:", shot)
        # A click in the panel: a raise request for buried A.
        Path(HOME, "requests").mkdir(exist_ok=True)
        Path(HOME, "requests", "t.json").write_text(json.dumps({"action": "raise", "session": a, "ts": time.time()}))
        t0 = time.time()
        for _ in range(20):
            await asyncio.sleep(0.1)
            tabs, bur = await snapshot(app, w.window_id)
            if a[:4] in tabs:
                break
        back_s = time.time() - t0
        await asyncio.sleep(0.5)
        await app.async_refresh_focus()
        cur = app.get_window_by_id(w.window_id).current_tab.current_session.session_id[:4]
        print(f"raise request -> A back after {back_s:.1f}s, selected: {cur == a[:4]}, "
              f"iTerm2 active: {app.app_active}", "(expect True, True)")
        # The click is a focus, not a pin: once the person leaves A, the normal rules apply.
        await app.get_tab_by_id(app.get_session_by_id(b).tab.tab_id).async_activate(order_window_front=False)
        await asyncio.sleep(8)
        tabs, bur = await snapshot(app, w.window_id)
        print("after raise, left A for 8s: A buried again?", a[:4] in bur, "(expect True: no pin)")
        hook(d, {"hook_event_name": "SessionEnd"}, agent="codex")
        for _ in range(40):
            await asyncio.sleep(0.25)
            tabs, bur = await snapshot(app, w.window_id)
            if d[:4] in tabs:
                break
        print("D ended while buried -> tabs, buried:", (tabs, bur), "(expect D back; only A buried)")
        # Seeding: agent tabs that sent no hook (started before the install). A tiny binary
        # named "codex" is the job (a copied /bin/sleep is killed by code signing; a job named
        # "claude" fires iTerm2's own Claude Code Workgroup trigger, which renames the tab).
        # The title glyph says idle or working.
        bindir = Path(HOME, "bin")
        bindir.mkdir(exist_ok=True)
        (bindir / "fake.c").write_text("#include <unistd.h>\nint main(void){ for(;;) pause(); }\n")
        subprocess.run(["cc", "-O", str(bindir / "fake.c"), "-o", str(bindir / "codex")], check=True)
        fakes = {}
        for title in ("✳ Fake idle agent", "◑ Fake working agent"):
            script = bindir / f"fake-{len(fakes)}.sh"
            script.write_text(f"printf '\\033]0;{title}\\007'\nexec {bindir / 'codex'}\n")
            await w.async_create_tab(command=f"/bin/sh {script}")
            await app.async_refresh()
            fakes[title] = app.get_window_by_id(w.window_id).tabs[-1].current_session.session_id
        await app.get_tab_by_id(app.get_session_by_id(b).tab.tab_id).async_activate(order_window_front=False)
        seeded = {}
        for _ in range(40):
            await asyncio.sleep(0.25)
            try:
                rows = json.loads(Path(HOME, "panel.json").read_text())["rows"]
            except (OSError, ValueError):
                continue
            names = {sid: title for title, sid in fakes.items()}
            seeded = {names[r["session"]]: (r["name"], r["state"], r["agent"]) for r in rows if r["session"] in names}
            if len(seeded) == 2:
                break
        print("seeded agent tabs in panel:", seeded,
              "(expect idle and working; agent codex)")
    finally:
        daemon.send_signal(signal.SIGTERM)
        daemon.wait(timeout=20)
        print("after SIGTERM tabs, buried:", await snapshot(app, w.window_id), "(expect nothing buried)")
        print("buried.json:", Path(HOME, "buried.json").read_text().strip())
        win = app.get_window_by_id(w.window_id)
        if win:
            await win.async_close(force=True)
        await asyncio.sleep(0.5)
        await app.async_refresh()
        after = outside(app, w.window_id)
        print("real tabs outside the test window unchanged:", after == before, "(expect True)")
        if after != before:
            print("  before:", before, "\n  after: ", after)


iterm2.run_until_complete(main)
