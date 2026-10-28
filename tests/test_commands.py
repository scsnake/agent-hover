import os
import sys

from agent_hover import commands, search

SID = "c0b348df-bf25-4841-9495-522a07ba6561"
CX = "01a18016-a27f-73f1-b2a3-3e350ad2c36c"


def test_claude_options_kept_session_ones_dropped():
    argv = ["claude", "--allow-dangerously-skip-permissions", "--model", "claude-opus-5-5", "--effort", "high",
            "--resume", "1669b56a-55ee-466e-97f2-f3f42bad2da3", "--add-dir", "/a", "/b b", "-n", "x",
            "--permission-mode=plan", "-w", "--debug", "api", "--settings", '{"a": 1}', "fix the loader"]
    assert commands.resume_args("claude", argv) == [
        "--allow-dangerously-skip-permissions", "--model", "claude-opus-5-5", "--effort", "high",
        "--add-dir", "/a", "/b b", "--permission-mode=plan", "--debug", "api", "--settings", '{"a": 1}']
    assert commands.resume_args("claude", ["claude", "-c", "--", "--model", "x"]) == []
    assert commands.resume_args("claude", ["node", "/x/claude", "--chrome", "hello"]) == ["--chrome"]


def test_codex_options():
    argv = ["/opt/homebrew/bin/codex", "-m", "gpt-5.6-luna", "resume", CX, "-s", "read-only", "-C", "/w",
            "-c", 'model_reasoning_effort="high"', "--dangerously-bypass-approvals-and-sandbox", "-i", "a.png",
            "b.png", "-mgpt-6", "go on"]
    assert commands.resume_args("codex", argv) == [
        "-m", "gpt-5.6-luna", "-s", "read-only", "-c", 'model_reasoning_effort="high"',
        "--dangerously-bypass-approvals-and-sandbox", "-mgpt-6"]


def test_not_a_session():
    assert commands.agent_index("codex", ["codex", "app-server", "--listen", "unix://"]) is None
    assert commands.agent_index("codex", ["codex", "-m", "x", "exec", "hi"]) is None
    assert commands.agent_index("claude", ["claude", "-p", "hi"]) is None
    assert commands.agent_index("claude", ["python", "x.py", "y", "claude"]) is None
    assert commands.agent_index("codex", ["node", "/x/bin/codex", "resume"]) == 1
    assert commands.agent_index("codex", ["codex", "-p", "work"]) == 0  # Codex -p: a profile
    # Option values are no subcommand ("plugin" is one).
    assert commands.agent_index("claude", ["claude", "--add-dir", "a", "plugin", "--debug", "plugin"]) == 0
    assert commands.agent_index("claude", ["claude", "--debug", "plugin"]) == 0
    assert commands.agent_index("claude", ["claude", "--model", "m", "plugin", "list"]) is None


def test_remember_and_resume(monkeypatch, tmp_path):
    monkeypatch.setattr(commands, "COMMANDS", tmp_path / "commands.json")
    argvs = {10: ["claude", "--model", "m", "--effort", "high", "hi"], 11: ["zsh"],
             12: ["codex", "-s", "read-only"], 13: ["claude", "--effort", "low"]}
    monkeypatch.setattr(commands, "process_argv", lambda pid: argvs.get(pid))
    assert search.resume_command("claude", SID, "") == f"claude --resume {SID}"  # nothing saved
    commands.remember({SID: ("claude", 10), CX: ("codex", 12), "not-a-uuid": ("claude", 10)}, now=1000.0)
    commands.remember({"6d44ce41-6712-4ff3-8cb0-c682a7a20711": ("claude", 11)}, now=1000.0)  # not claude
    assert set(commands.load()) == {SID, CX}
    assert search.resume_command("claude", SID, "/p q") == f"cd '/p q' && claude --model m --effort high --resume {SID}"
    assert search.resume_command("codex", CX, "") == f"codex resume -s read-only {CX}"
    commands.remember({SID: ("claude", 13)}, now=2000.0)  # resumed later with other options
    assert search.resume_command("claude", SID, "") == f"claude --effort low --resume {SID}"
    commands.remember({}, now=2000.0 + commands.KEEP_DAYS * 86400 + 1)  # old entries go
    assert commands.load() == {}


def test_process_argv_and_parents():
    argv = commands.process_argv(os.getpid())
    assert argv and "python" in os.path.basename(argv[0]).lower() or argv[0] == sys.executable
    assert commands.parent_pids(os.getpid())[:2] == [os.getpid(), os.getppid()]


def test_remember_together_keeps_both(monkeypatch, tmp_path):
    import threading
    import time as t
    monkeypatch.setattr(commands, "COMMANDS", tmp_path / "commands.json")
    monkeypatch.setattr(commands, "process_argv", lambda pid: ["claude", "--effort", str(pid)])
    real_load = commands.load

    def slow_load():
        data = real_load()
        t.sleep(0.05)  # both would read the same old file without the lock
        return data

    monkeypatch.setattr(commands, "load", slow_load)
    sids = [SID, "6d44ce41-6712-4ff3-8cb0-c682a7a20711"]
    threads = [threading.Thread(target=commands.remember, args=({s: ("claude", 10 + i)},)) for i, s in enumerate(sids)]
    [th.start() for th in threads]
    [th.join() for th in threads]
    assert set(real_load()) == set(sids)
