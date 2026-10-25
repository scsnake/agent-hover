import json
import re
import shlex

from agent_hover import daemon, remote, states

SID = "d9541dca-fd05-4871-8f33-8b2eb241a824"
CODEX_SID = "01a16727-88f9-7c72-85b6-30d19b5ef933"
NOW = 1792750000.0


def pane(pid, pane_id, session, window, shell, path, title, active="1"):
    values = {"pid": pid, "pane_id": pane_id, "session_name": session, "window_index": window, "pane_pid": shell,
              "pane_active": active, "window_active": active, "session_attached": "1",
              "pane_current_path": path, "window_name": "zsh", "pane_title": title}
    return remote.SEP.join(values[f] for f in remote.FIELDS)


def scan_output(codex_tail):
    m = remote.MARK
    claude = {"pid": 300, "sessionId": SID, "cwd": "/data/proj", "status": "busy", "statusUpdatedAt": NOW * 1000 - 5000,
              "tmux": "a:@4.%4", "name": "proj-1", "entrypoint": "cli"}
    stale = {"pid": 999, "sessionId": SID, "status": "idle"}  # no such process
    return "\n".join([
        f"{m} host gpu-box.lan",
        f"{m} panes",
        pane("2538", "%4", "a", "3", "200", "/data/proj", "⠂ Fix the loader"),
        pane("2538", "%5", "a", "4", "210", "/data/other", "gpu-box", active="0"),
        pane("2538", "%6", "b", "1", "220", "/home/me", "gpu-box"),
        f"{m} ps",
        "200 1 0.0 4000 zsh -zsh",
        "300 200 12.5 300000 claude claude --resume",
        "301 300 2.0 50000 node node mcp.js",
        "210 1 0.0 4000 zsh -zsh",
        "400 210 1.0 90000 codex codex",
        "220 1 0.0 4000 zsh -zsh",
        f"{m} claude", json.dumps(claude),
        f"{m} claude", json.dumps(stale),
        f"{m} codex 400 {int(NOW) - 30} /home/me/.codex/sessions/2026/10/23/rollout-2026-10-23T13-06-46-{CODEX_SID}.jsonl",
        codex_tail,
        f"{m} model {SID}", '"model":"claude-opus-5-5"',
        f"{m} end", ""])


def codex_limit_tail():
    limits = {"timestamp": "x", "type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
        "primary": {"used_percent": 99.0, "resets_at": NOW + 600}, "secondary": {"used_percent": 40.0, "resets_at": NOW + 9000}}}}
    done = {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t1", "completed_at": NOW - 60,
                                             "error": {"codex_error_info": "usage_limit_exceeded",
                                                       "message": "You've hit your usage limit."}}}
    return 'cut first line"}\n' + json.dumps(limits) + "\n" + json.dumps(done)


def test_no_connect_no_host():
    said = []
    hosts = remote.load_hosts({"remote": {"hosts": [{"name": "a"}, {"name": "b", "connect": []},
                                                    {"name": "bad name", "connect": ["ssh", "x"]}]}},
                              log=lambda kind, **kw: said.append((kind, kw["why"])))
    assert hosts == [] and [w for _, w in said] == ["no connect command"] * 2 + ["bad or repeated name"]
    assert remote.load_hosts({}) == []


def test_ssh_options_only_when_not_set():
    (h,) = remote.load_hosts({"remote": {"hosts": [{"name": "gpu", "connect": ["ssh", "-o", "ConnectTimeout=20", "gpu"]}]}})
    assert h.connect[0] == "ssh" and h.connect[-1] == "gpu"
    opts = " ".join(h.connect)
    assert "ConnectTimeout=20" in opts and "ConnectTimeout=5" not in opts
    assert "BatchMode=yes" in opts and "ControlMaster=auto" in opts and "Compression=yes" in opts
    # The attach command comes from the person's own connect command, without the added options.
    assert h.attach == "ssh -o ConnectTimeout=20 -t gpu " + shlex.quote(remote.ATTACH_TMUX)
    (plain,) = remote.load_hosts({"remote": {"hosts": [{"name": "w", "connect": ["ssh", "w"], "share_connection": False}]}})
    assert "ControlMaster" not in " ".join(plain.connect)
    (other,) = remote.load_hosts({"remote": {"hosts": [{"name": "t", "connect": ["et", "t"]}]}})
    assert other.connect == ["et", "t"] and other.attach is None


def test_attach_command_fills_safe_values():
    (h,) = remote.load_hosts({"remote": {"hosts": [{"name": "gpu", "connect": ["ssh", "gpu"],
                                                    "attach": "ssh -t gpu tmux attach -t {session} {window} {pane}"}]}})
    row = {"pane": "%4", "window": "3", "tmux_session": "a; rm -rf x"}
    assert h.attach_command(row) == "ssh -t gpu tmux attach -t 'a; rm -rf x' 3 %4"
    assert h.attach_command({**row, "pane": "%4; ls"}) is None


def test_script_reads_only_and_quotes_paths():
    script = remote.collect_script([("claude", SID, None), ("codex", CODEX_SID, "/tmp/a b'c.jsonl"),
                                    ("claude", "../../etc", None)])
    assert "/tmp/a b'\"'\"'c.jsonl" in script and "../../etc" not in script
    for word in ("rm ", "mv ", "tee", "kill", "send-keys", "touch", "mkdir"):
        assert word not in script
    # No file is written: the only redirect sends errors to /dev/null ("->" is sed text).
    assert re.findall(r">", script.replace("2>/dev/null", "").replace(" -> ", "")) == []


def test_rows_from_scan():
    parsed = remote.parse_output(scan_output(codex_limit_tail()))
    assert parsed["complete"] and parsed["host"] == "gpu-box.lan" and parsed["models"] == {SID: "claude-opus-5-5"}
    rows = remote.build_rows(parsed, NOW)
    assert set(rows) == {"tmux-2538-4", "tmux-2538-5"}  # pane %6 has no agent
    c = rows["tmux-2538-4"]
    assert (c["agent"], c["state"], c["cwd"], c["agent_session"], c["name"]) == (
        "claude", states.WORKING, "/data/proj", SID, "Fix the loader")
    assert c["since"] == NOW - 5 and c["cpu"] == 14.5 and c["active"]
    x = rows["tmux-2538-5"]
    assert (x["agent"], x["state"], x["detail"]) == ("codex", states.WAITING, "usage limit")
    assert x["resets_at"] == NOW + 600 and x["question"].startswith("Codex hit its usage limit.")
    assert x["name"] == "other" and x["cwd"] == "/data/other" and not x["active"]  # title is the host name


def test_incomplete_scan_is_not_used():
    parsed = remote.parse_output(scan_output("")[:200])
    assert not parsed["complete"]


def test_remote_done_until_seen(monkeypatch):
    sent = []
    monkeypatch.setattr(daemon, "notify", lambda title, text: sent.append(text))
    monkeypatch.setattr(daemon, "log", lambda *a, **k: None)

    class FakeHost:
        name = "gpu"
        rows = {}

        def fresh_rows(self, now):
            return self.rows

    h = FakeHost()
    base = {"key": "tmux-1-4", "pane": "%4", "tmux_session": "a", "window": "3", "name": "Fix", "agent": "claude",
            "cwd": "/p", "since": NOW - 100, "active": True}
    last_focus, done_at, notified = {}, {}, set()

    def tick(now, focus_tag="", **row):
        h.rows = {"tmux-1-4": {**base, **row}}
        return daemon.remote_panel_rows([h], now, NOW - 1000, focus_tag, last_focus, set(), done_at, notified, True)[0]

    assert tick(NOW, state="working")["state"] == "working"
    r = tick(NOW + 5, state="idle", since=NOW + 4)
    assert (r["state"], r["unseen"], r["session"], r["place"], r["host"]) == ("done", True, "gpu:tmux-1-4", "a:3", "gpu")
    assert tick(NOW + 10, focus_tag="gpu:a", state="idle", since=NOW + 4)["focused"]
    assert tick(NOW + 15, state="idle", since=NOW + 4)["state"] == "idle"  # seen: back to idle
    # An idle agent that was never seen working stays idle.
    last_focus.clear(), done_at.clear()
    assert tick(NOW + 20, state="idle", since=NOW - 900)["state"] == "idle"
    # A usage limit that is over: one notification.
    tick(NOW + 25, state="waiting", resets_at=NOW + 24)
    tick(NOW + 30, state="waiting", resets_at=NOW + 24)
    assert len(sent) == 1


def test_split_sid():
    assert remote.split_sid("gpu:tmux-1-4") == ("gpu", "tmux-1-4")
    assert remote.split_sid("243CB74F-A646-4BFE-A2AB-E462597C654F") is None
    assert remote.split_sid("gpu:../x") is None


def test_clients_and_local_ssh_ends(monkeypatch):
    out = "\n".join(["2258093|~|a", "1852973|~|claude bot", "77|~|local", "88|~|old", f"{remote.MARK} ssh",
                     "2258093 10326 SSH_CONNECTION=192.0.2.10 56549 198.51.100.20 22",
                     "1852973 14628 SSH_CONNECTION=fe80::1 64759 fe80::2 22", "77  ",
                     "88  SSH_CONNECTION=10.0.0.1 5000 10.0.0.2 22"])
    assert remote.parse_clients(out) == [("a", "192.0.2.10", 56549, 10326.0),
                                         ("claude bot", "fe80::1", 64759, 14628.0), ("old", "10.0.0.1", 5000, None)]
    assert [remote.elapsed_s(x) for x in ("02:52:08", "04:03", "1-00:00:01", "x", "1:2:3:4")] == [
        10328.0, 243.0, 86401.0, None, None]

    class R:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(cmd, **kw):
        if cmd[0] == "ps":
            return R("52680 02:52:08 ttys000 ssh\n52569 02:52:09 ttys000 -zsh\n60000 04:03:49 ttys009 /usr/bin/ssh\n"
                     "61000 04:25:40 ?? ssh: /Users/me/.ssh/cp-d890 [mux]\n62000 01:00 ttys012 ssh\n")
        assert cmd[cmd.index("-p") + 1] == "52680,60000,61000,62000"
        return R("p52680\nf4\ntIPv4\nd0x30\nn192.0.2.10:56549->198.51.100.20:22\n"
                 "p60000\nf3\ntunix\nd0xcc\nn->0xe8\n"  # a ControlMaster client: no TCP of its own
                 "p61000\nf4\ntIPv6\nd0x39\nn[fe80::1]:64759->[fe80::2]:22\nf10\ntunix\nd0xe8\nn/Users/me/.ssh/cp-d890\n"
                 "p62000\nf3\ntunix\nd0xdd\nn->0xff\n")  # its master is gone

    monkeypatch.setattr(remote.subprocess, "run", fake_run)
    assert remote.local_ssh_ends({"/dev/ttys000", "/dev/ttys009", "/dev/ttys012"}) == {
        "/dev/ttys000": {("192.0.2.10", 56549): 10328.0}, "/dev/ttys009": {("fe80::1", 64759): 14629.0}}


def test_match_tab():
    master = ("10.0.0.1", 64759)
    clients = [("a", "10.0.0.1", 56549, 10326.0), ("bot", *master, 14628.0), ("c", *master, 300.0)]
    local = {"/dev/ttys000": {("10.0.0.1", 56549): 10328.0},
             "/dev/ttys009": {master: 14629.0}, "/dev/ttys010": {master: 301.0}, "/dev/ttys011": {master: 50.0}}
    assert remote.match_tab(clients, local, "a") == "/dev/ttys000"
    # Three tabs share one ControlMaster connection: the ages tell which one runs which client.
    assert remote.match_tab(clients, local, "bot") == "/dev/ttys009"
    assert remote.match_tab(clients, local, "c") == "/dev/ttys010"
    assert remote.match_tab(clients, local, "none") is None
    # One tab on the connection, one client: no age needed. Two tabs, no age: no guess.
    assert remote.match_tab([("bot", *master, None)], {"/dev/ttys009": {master: 1.0}}, "bot") == "/dev/ttys009"
    assert remote.match_tab([("bot", *master, None)], local, "bot") is None
    assert remote.match_tab([("bot", *master, 9000.0)], local, "bot") is None  # no tab that old
