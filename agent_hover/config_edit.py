"""Add or remove the agent-hover hooks in Claude Code and Codex settings, with backups.

  python -m agent_hover.config_edit install [--apply]
  python -m agent_hover.config_edit uninstall [--apply]
  python -m agent_hover.config_edit restore [--apply] [--force] [--backup DIR]

Without --apply nothing is written; the planned change is printed.
install   backs up both files (exact bytes) to ~/.agent-hover/backup/<time>/, then adds only
          entries tagged with "agent_hover/hook.py". Other hooks, cc-status included, stay.
uninstall removes only the tagged entries; all other later changes stay.
restore   copies the exact backup back. It refuses if the file changed after install,
          unless --force (later changes would be lost).
"""
import argparse
import difflib
import hashlib
import json
import os
import shutil
import stat
import sys
import time
import tomllib
from pathlib import Path

from agent_hover import states

PROJ = Path(__file__).resolve().parent.parent
TAG = "agent_hover/hook.py"
CLAUDE_FILE = Path.home() / ".claude" / "settings.json"
CODEX_FILE = Path.home() / ".codex" / "config.toml"
CLAUDE_EVENTS = ["SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PermissionRequest",
                 "PostToolUse", "Notification", "Stop", "StopFailure"]
CODEX_EVENTS = ["SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PermissionRequest",
                "PostToolUse", "Stop", "Interrupt"]
CODEX_SHORT_TIMEOUT = {"SessionEnd", "Interrupt"}  # Codex allows max 3 s for these
BEGIN = "# >>> agent-hover (POC 1) hooks >>>"
END = "# <<< agent-hover (POC 1) hooks <<<"


def python_path():
    for p in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3", shutil.which("python3")):
        if p and Path(p).exists():
            return p
    sys.exit("python3 not found")


def hook_cmd(agent):
    return f"'{python_path()}' -I '{PROJ}/agent_hover/hook.py' {agent}"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def show_diff(name, old, new):
    diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), f"{name} (now)", f"{name} (planned)",
                                     lineterm="", n=1))
    print("\n".join(diff) if diff else f"{name}: no change")


# ---- Claude Code (JSON) -------------------------------------------------------------

def claude_without_ours(data):
    hooks = data.get("hooks", {})
    for event in list(hooks):
        groups = []
        for g in hooks[event]:
            kept = [h for h in g.get("hooks", []) if TAG not in h.get("command", "")]
            if kept:
                groups.append({**g, "hooks": kept})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    return data


def claude_with_ours(data):
    data = claude_without_ours(data)
    hooks = data.setdefault("hooks", {})
    for event in CLAUDE_EVENTS:
        hooks.setdefault(event, []).append({"hooks": [{"type": "command", "command": hook_cmd("claude"),
                                                        "timeout": 5}]})
    return data


# ---- Codex (TOML, a marked block at the end) -----------------------------------------

def codex_without_ours(text):
    if BEGIN not in text:
        return text
    head, rest = text.split(BEGIN, 1)
    tail = rest.split(END, 1)[1] if END in rest else ""
    return head.rstrip("\n") + "\n" + tail.lstrip("\n")


def codex_with_ours(text):
    text = codex_without_ours(text)
    cmd = hook_cmd("codex").replace("\\", "\\\\").replace('"', '\\"')
    lines = ["", BEGIN]
    for event in CODEX_EVENTS:
        timeout = 2 if event in CODEX_SHORT_TIMEOUT else 5
        lines += [f"[[hooks.{event}]]", "", f"[[hooks.{event}.hooks]]", 'type = "command"',
                  f'command = "{cmd}"', f"timeout = {timeout}", ""]
    lines.append(END)
    return text.rstrip("\n") + "\n" + "\n".join(lines) + "\n"


# ---- planning and writing ------------------------------------------------------------

def plan(mode):
    """Return [(path, old_bytes, new_bytes, display_old, display_new)]."""
    out = []
    if CLAUDE_FILE.exists():
        raw = CLAUDE_FILE.read_bytes()
        old = json.loads(raw)
        new = claude_with_ours(json.loads(raw)) if mode == "install" else claude_without_ours(json.loads(raw))
        new_bytes = (json.dumps(new, indent=2, ensure_ascii=False) + "\n").encode()
        if new != old:
            out.append((CLAUDE_FILE, raw, new_bytes, json.dumps(old, indent=2, ensure_ascii=False),
                        new_bytes.decode()))
    if CODEX_FILE.exists():
        raw = CODEX_FILE.read_bytes()
        text = raw.decode()
        tomllib.loads(text)
        new = codex_with_ours(text) if mode == "install" else codex_without_ours(text)
        tomllib.loads(new)  # refuse to write a file Codex cannot read
        if new != text:
            out.append((CODEX_FILE, raw, new.encode(), text, new))
    return out


def write(path, data, expect=None, mode=None):
    """Replace path atomically and keep its permission bits (or use mode).

    expect: the bytes the plan was made from. If the file changed since then (Claude Code,
    Codex or iTerm2 wrote it), refuse, so their new content is not lost."""
    if expect is not None and path.read_bytes() != expect:
        sys.exit(f"Refused: {path} changed while this ran. Nothing more written. Run the command again.")
    if mode is None:
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    tmp = path.with_suffix(path.suffix + ".agent-hover-tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(tmp, mode)
    tmp.replace(path)


def cmd_install(apply):
    changes = plan("install")
    for path, _, _, a, b in changes:
        show_diff(str(path), a, b)
    if CLAUDE_FILE in [c[0] for c in changes]:
        print("\nNote: settings.json is written in standard JSON layout. The content is the same; "
              "the backup keeps the exact old bytes.")
    if not changes:
        print("Already installed. Nothing to do.")
        return
    if not apply:
        print("\nDry run. Nothing written. Run again with --apply to install.")
        return
    bdir = states.BACKUP_DIR / time.strftime("%Y%m%d-%H%M%S")
    bdir.mkdir(parents=True)
    # Backups and the manifest come first, so a crash after one file is written can be restored.
    manifest = {}
    for path, old, new, _, _ in changes:
        (bdir / path.name).write_bytes(old)
        shutil.copymode(path, bdir / path.name)
        manifest[str(path)] = {"backup": str(bdir / path.name), "sha_before": sha(old), "sha_written": sha(new)}
    (bdir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    for path, old, new, _, _ in changes:
        write(path, new, expect=old)
    print(f"\nInstalled. Backup: {bdir}")


def cmd_uninstall(apply):
    changes = plan("uninstall")
    for path, _, _, a, b in changes:
        show_diff(str(path), a, b)
    if not changes:
        print("No agent-hover hooks found. Nothing to do.")
        return
    if not apply:
        print("\nDry run. Nothing written. Run again with --apply to remove the agent-hover hooks.")
        return
    for path, old, new, _, _ in changes:
        write(path, new, expect=old)
    print("\nRemoved the agent-hover hooks. Other settings are unchanged.")


def cmd_restore(apply, force, backup):
    dirs = sorted(states.BACKUP_DIR.glob("*/manifest.json"))
    if backup:
        dirs = [Path(backup) / "manifest.json"]
    if not dirs:
        sys.exit("No backup found.")
    manifest = json.loads(dirs[-1].read_text())
    print(f"Backup: {dirs[-1].parent}")
    blocked, todo = False, []
    for path, info in manifest.items():
        p = Path(path)
        now = p.read_bytes() if p.exists() else b""
        old = Path(info["backup"]).read_bytes()
        if sha(now) == info["sha_before"]:
            print(f"\n{path}: same as the backup (install did not change it); nothing to restore")
            continue
        changed_after = sha(now) != info["sha_written"]
        state = "changed after install" if changed_after else "unchanged since install"
        print(f"\n{path}: {state}")
        show_diff(path, now.decode(errors="replace"), old.decode(errors="replace"))
        todo.append((p, now, Path(info["backup"])))
        if changed_after and not force:
            blocked = True
    if blocked:
        print("\nRefused: a file changed after install, and restore would lose those changes.\n"
              "Use 'uninstall' (removes only our hooks), or add --force to restore anyway.")
        return
    if not todo:
        print("\nNothing to restore.")
        return
    if not apply:
        print("\nDry run. Nothing written. Run again with --apply to restore the exact backup.")
        return
    for p, now, backup in todo:
        write(p, backup.read_bytes(), expect=now, mode=stat.S_IMODE(backup.stat().st_mode))
    print("\nRestored the exact backup (content and permissions).")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["install", "uninstall", "restore"])
    p.add_argument("--apply", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--backup")
    a = p.parse_args()
    if a.command == "install":
        cmd_install(a.apply)
    elif a.command == "uninstall":
        cmd_uninstall(a.apply)
    else:
        cmd_restore(a.apply, a.force, a.backup)


if __name__ == "__main__":
    main()
