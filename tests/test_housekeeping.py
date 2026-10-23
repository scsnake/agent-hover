import json
import subprocess

from agent_hover import daemon, states


def test_log_rotates_over_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(states, "LOG_DIR", tmp_path)
    monkeypatch.setattr(states, "LOG_MAX_BYTES", 100)
    for i in range(10):
        states.append_log("events.jsonl", {"i": i, "pad": "x" * 20})
    old = (tmp_path / "events.jsonl.1").read_text().splitlines()
    new = (tmp_path / "events.jsonl").read_text().splitlines()
    assert old and new and len(old) + len(new) <= 10
    assert json.loads(new[-1])["i"] == 9


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def commit(repo, msg):
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", msg)


def test_check_update(tmp_path):
    origin, local = tmp_path / "origin.git", tmp_path / "local"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(local)], check=True, capture_output=True)
    commit(local, "one")
    git(local, "push", "-q", "origin", "HEAD")
    assert daemon.check_update(local)["available"] is False  # same commit
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, capture_output=True)
    commit(other, "two")
    git(other, "push", "-q", "origin", "HEAD")
    assert daemon.check_update(local)["available"] is True  # remote is ahead
    commit(local, "local work")
    git(local, "-c", "user.name=t", "-c", "user.email=t@t", "pull", "-q", "--no-rebase", "--no-edit", "origin", "HEAD")
    assert daemon.check_update(local)["available"] is False  # remote commit already merged here
    assert daemon.check_update(tmp_path) is None  # not a checkout with a remote
