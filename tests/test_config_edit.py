import json
import tomllib

from agent_hover import config_edit as ce

CLAUDE = {
    "model": "x",
    "hooks": {
        "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "printf '\\a'"}]},
                 {"hooks": [{"type": "command", "command": "/Users/u/.config/iterm2/cc-status"}]}],
        "PreToolUse": [{"hooks": [{"type": "command", "command": "/Users/u/.config/iterm2/cc-status"}]}],
    },
}

CODEX = '''model = "m"

[[hooks.Stop]]

[[hooks.Stop.hooks]]
type = "command"
command = "python3 stamp.py"
timeout = 5

[projects."/a"]
trust_level = "trusted"
'''


def test_claude_install_keeps_other_hooks_and_uninstall_is_exact():
    before = json.loads(json.dumps(CLAUDE))
    installed = ce.claude_with_ours(json.loads(json.dumps(CLAUDE)))
    assert all(any(ce.TAG in h["command"] for g in installed["hooks"][e] for h in g["hooks"])
               for e in ce.CLAUDE_EVENTS)
    cmds = [h["command"] for g in installed["hooks"]["Stop"] for h in g["hooks"]]
    assert "/Users/u/.config/iterm2/cc-status" in cmds and "printf '\\a'" in cmds
    again = ce.claude_with_ours(json.loads(json.dumps(installed)))
    assert again == installed  # install twice = once
    assert ce.claude_without_ours(installed) == before


def test_codex_block_is_valid_toml_and_removable():
    new = ce.codex_with_ours(CODEX)
    data = tomllib.loads(new)
    assert len(data["hooks"]["Stop"]) == 2
    assert data["hooks"]["Interrupt"][0]["hooks"][0]["timeout"] == 2
    assert ce.codex_with_ours(new) == new
    assert tomllib.loads(ce.codex_without_ours(new)) == tomllib.loads(CODEX)


def _sandbox(tmp_path, monkeypatch):
    claude, codex = tmp_path / "settings.json", tmp_path / "config.toml"
    claude.write_text(json.dumps(CLAUDE))
    codex.write_text(CODEX)
    claude.chmod(0o644)
    codex.chmod(0o600)
    monkeypatch.setattr(ce, "CLAUDE_FILE", claude)
    monkeypatch.setattr(ce, "CODEX_FILE", codex)
    monkeypatch.setattr(ce.states, "BACKUP_DIR", tmp_path / "backup")
    return claude, codex


def test_install_uninstall_restore_keep_permissions(tmp_path, monkeypatch):
    claude, codex = _sandbox(tmp_path, monkeypatch)
    before = codex.read_bytes(), claude.read_bytes()
    ce.cmd_install(apply=True)
    assert oct(codex.stat().st_mode & 0o777) == "0o600"
    assert oct(claude.stat().st_mode & 0o777) == "0o644"
    assert ce.TAG in codex.read_text()
    ce.cmd_restore(apply=True, force=False, backup=None)
    assert (codex.read_bytes(), claude.read_bytes()) == before
    assert oct(codex.stat().st_mode & 0o777) == "0o600"


def test_write_refuses_when_file_changed_meanwhile(tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"new content from Claude")
    try:
        ce.write(p, b"ours", expect=b"old content")
    except SystemExit:
        pass
    else:
        raise AssertionError("write should refuse")
    assert p.read_bytes() == b"new content from Claude"


def test_restore_after_partial_install(tmp_path, monkeypatch):
    claude, codex = _sandbox(tmp_path, monkeypatch)
    real_write = ce.write
    calls = []

    def crash_on_second(path, data, **kw):
        calls.append(path)
        if len(calls) == 2:
            raise KeyboardInterrupt("crash")
        real_write(path, data, **kw)

    monkeypatch.setattr(ce, "write", crash_on_second)
    before = claude.read_bytes(), codex.read_bytes()
    try:
        ce.cmd_install(apply=True)
    except KeyboardInterrupt:
        pass
    monkeypatch.setattr(ce, "write", real_write)
    ce.cmd_restore(apply=True, force=False, backup=None)  # manifest exists although install crashed
    assert (claude.read_bytes(), codex.read_bytes()) == before


def test_statusline_wrapper_round_trip():
    orig = {"statusLine": {"type": "command", "command": "bash '/x/statusline.sh' | cat", "refreshInterval": 60}}
    wrapped = ce.statusline_with_ours(json.loads(json.dumps(orig)), "claude")
    cmd = wrapped["statusLine"]["command"]
    assert ce.QUOTA_TAG in cmd and cmd.endswith("-- sh -c 'bash '\"'\"'/x/statusline.sh'\"'\"' | cat'")
    assert wrapped["statusLine"]["refreshInterval"] == 60
    assert ce.statusline_with_ours(json.loads(json.dumps(wrapped)), "claude") == wrapped  # twice = once
    assert ce.statusline_without_ours(wrapped) == orig
    assert ce.statusline_with_ours({"model": "x"}, "agy") == {"model": "x"}  # no status line: none added
    fresh = ce.statusline_with_ours(json.loads(json.dumps(orig)), "claude")
    edited = {"statusLine": {**fresh["statusLine"], "command": fresh["statusLine"]["command"] + " | tee /tmp/x"}}
    assert ce.statusline_without_ours(json.loads(json.dumps(edited))) == edited  # edited: left as it is
    assert ce.statusline_with_ours(json.loads(json.dumps(edited)), "claude") == edited  # not wrapped twice
    broken = {"statusLine": {**fresh["statusLine"], "command": fresh["statusLine"]["command"] + " 'x"}}
    assert ce.statusline_without_ours(json.loads(json.dumps(broken))) == broken  # unbalanced quote: no crash


def test_install_backs_up_two_settings_files(tmp_path, monkeypatch):
    claude, agy = tmp_path / "claude" / "settings.json", tmp_path / "agy" / "settings.json"
    for p, cmd in ((claude, "claude-line"), (agy, "agy-line")):
        p.parent.mkdir()
        p.write_text(json.dumps({"statusLine": {"type": "command", "command": cmd}}))
    monkeypatch.setattr(ce, "CLAUDE_FILE", claude)
    monkeypatch.setattr(ce, "CODEX_FILE", tmp_path / "none.toml")
    monkeypatch.setattr(ce, "AGY_SETTINGS", agy)
    monkeypatch.setattr(ce.states, "BACKUP_DIR", tmp_path / "backup")
    ce.cmd_install(True)
    assert ce.QUOTA_TAG in json.loads(agy.read_text())["statusLine"]["command"]
    (bdir,) = (tmp_path / "backup").iterdir()
    manifest = json.loads((bdir / "manifest.json").read_text())
    assert {json.loads(open(v["backup"]).read())["statusLine"]["command"] for v in manifest.values()} == {
        "claude-line", "agy-line"}
    ce.cmd_uninstall(True)
    assert json.loads(agy.read_text())["statusLine"]["command"] == "agy-line"
    assert json.loads(claude.read_text())["statusLine"]["command"] == "claude-line"
