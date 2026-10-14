"""Выкатка deploy/deploy.sh: только коммит из main, smoke по PID, откат при падении.

systemctl, sudo и pip подменены скриптами в PATH; сервер — клон временного репозитория.
"""
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "deploy.sh"

FAKE_SYSTEMCTL = """#!/usr/bin/env bash
# restart — новый PID; при файле CRASH в рабочей копии PID меняется на каждом show (рестарт-петля).
st="$FAKE_STATE"
case "$1" in
  restart) echo restart >> "$st/calls"; echo $(( $(cat "$st/pid" 2>/dev/null || echo 100) + 1 )) > "$st/pid" ;;
  is-active) echo active ;;
  show)
    if [ -e "$FLIGHT_BOT_DIR/CRASH" ]; then echo $(( $(cat "$st/pid") + 1 )) > "$st/pid"; fi
    cat "$st/pid" ;;
esac
"""
FAKE_SUDO = """#!/usr/bin/env bash
[ "$1" = -n ] && shift
exec "$@"
"""
FAKE_PIP = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_STATE/pip"
"""


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(repo, name, text):
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", name)
    return _git(repo, "rev-parse", "HEAD")


def _exe(path, text):
    path.write_text(text)
    path.chmod(0o755)


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(k, "t")
    for k in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(k, "t@t")
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-qb", "main")
    first = _commit(work, "requirements.txt", "aiogram\n")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(work), str(origin))
    _git(work, "remote", "add", "origin", str(origin))
    server = tmp_path / "server"
    _git(tmp_path, "clone", "-q", str(origin), str(server))
    (server / ".venv" / "bin").mkdir(parents=True)
    _exe(server / ".venv" / "bin" / "pip", FAKE_PIP)
    (server / ".git" / "info" / "exclude").write_text(".venv/\n")
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    _exe(fakebin / "systemctl", FAKE_SYSTEMCTL)
    _exe(fakebin / "sudo", FAKE_SUDO)
    state = tmp_path / "state"
    state.mkdir()
    (state / "pid").write_text("100\n")
    return {"work": work, "server": server, "state": state, "first": first,
            "env": {**os.environ, "PATH": f"{fakebin}:{os.environ['PATH']}",
                    "FLIGHT_BOT_DIR": str(server), "FAKE_STATE": str(state), "SMOKE_SEC": "1",
                    "DEPLOY_LOCK": str(tmp_path / "deploy.lock")}}


def _push(e, name, text):
    sha = _commit(e["work"], name, text)
    _git(e["work"], "push", "-q", "origin", "main")
    return sha


def _deploy(e, arg):
    return subprocess.run(["bash", str(SCRIPT)], env={**e["env"], "SSH_ORIGINAL_COMMAND": arg},
                          capture_output=True, text=True, timeout=60)


def _calls(e, name):
    f = e["state"] / name
    return f.read_text().splitlines() if f.exists() else []


def _head(e):
    return _git(e["server"], "rev-parse", "HEAD")


def test_deploys_main_commit_and_restarts(env):
    sha = _push(env, "bot.py", "v2\n")
    r = _deploy(env, sha)
    assert r.returncode == 0, r.stderr
    assert _head(env) == sha
    assert _calls(env, "calls") == ["restart"]
    assert _calls(env, "pip") == []                 # requirements.txt не менялся


def test_reinstalls_deps_when_requirements_change(env):
    sha = _push(env, "requirements.txt", "aiogram\nhttpx\n")
    r = _deploy(env, sha)
    assert r.returncode == 0, r.stderr
    assert _calls(env, "pip") == ["install -q -r requirements.txt"]


@pytest.mark.parametrize("arg", ["", "main", "abc123", "0" * 39 + "; id", "--help"])
def test_rejects_anything_but_full_sha(env, arg):
    r = _deploy(env, arg)
    assert r.returncode == 2
    assert _head(env) == env["first"]
    assert _calls(env, "calls") == []


def test_rejects_commit_not_in_main(env):
    _git(env["work"], "checkout", "-qb", "feature")
    sha = _commit(env["work"], "bot.py", "evil\n")
    _git(env["work"], "push", "-q", "origin", "feature")
    _git(env["server"], "fetch", "-q", "origin", "feature")   # объект коммита на сервере есть
    r = _deploy(env, sha)
    assert r.returncode != 0
    assert _head(env) == env["first"]
    assert _calls(env, "calls") == []


def test_rolls_back_when_service_keeps_restarting(env):
    sha = _push(env, "CRASH", "boom\n")
    r = _deploy(env, sha)
    assert r.returncode == 1
    assert _head(env) == env["first"]               # откат на прежний коммит
    assert not (env["server"] / "CRASH").exists()
    assert _calls(env, "calls") == ["restart", "restart"]


def test_already_deployed_commit_is_noop(env):
    old = _push(env, "bot.py", "v2\n")
    new = _push(env, "bot.py", "v3\n")
    assert _deploy(env, new).returncode == 0
    r = _deploy(env, old)                           # запоздавший запуск со старым коммитом
    assert r.returncode == 0, r.stderr
    assert _head(env) == new
    assert _calls(env, "calls") == ["restart"]


def test_refuses_local_changes_on_server(env):
    sha = _push(env, "bot.py", "v2\n")
    (env["server"] / "requirements.txt").write_text("hand-edited\n")
    r = _deploy(env, sha)
    assert r.returncode != 0
    assert _head(env) == env["first"]
    assert (env["server"] / "requirements.txt").read_text() == "hand-edited\n"
    assert _calls(env, "calls") == []
