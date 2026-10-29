"""Unit tests for jump/server/jump-server (offline: fake wg, JUMP_NO_APPLY=1)."""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "jump" / "server" / "jump-server"
SHELL_SCRIPTS = {
    "bash": [
        "jump/server/jump-forward",
        "jump/server/install.sh",
        "jump/laptop-a/install.sh",
        "jump/laptop-b/install.sh",
        "jump/laptop-b/jump-exit-nat",
    ],
    "sh": ["jump/router-a/setup.sh", "jump/router-b/setup.sh"],
}


@pytest.fixture
def hub(tmp_path, monkeypatch):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    wg = fake_bin / "wg"
    wg.write_text(textwrap.dedent("""\
        #!/bin/sh
        case "$1" in
          genkey) echo "PRIV$(od -An -N6 -tx1 /dev/urandom | tr -d ' \\n')" ;;
          pubkey) read x; echo "PUB${x#PRIV}" ;;
          *) exit 0 ;;
        esac
    """))
    wg.chmod(0o755)
    env = dict(os.environ)
    env.update({
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "JUMP_CONFIG_DIR": str(tmp_path / "config"),
        "JUMP_SECRETS_DIR": str(tmp_path / "secrets"),
        "JUMP_WG_CONF": str(tmp_path / "wg" / "dromos-jump.conf"),
        "JUMP_NO_APPLY": "1",
    })

    def run(*args, ok=True):
        proc = subprocess.run([str(SCRIPT), *args], env=env, capture_output=True, text=True)
        if ok:
            assert proc.returncode == 0, proc.stderr
        return proc

    run.tmp = tmp_path
    return run


def test_init_requires_endpoint(hub):
    proc = hub("init", ok=False)
    assert proc.returncode != 0
    assert "--endpoint" in proc.stderr


def test_server_config_routes_exit_and_clients(hub):
    hub("init", "--endpoint", "203.0.113.7")
    hub("add", "b-exit", "--role", "exit")
    hub("add", "a-laptop", "--role", "client")
    hub("add", "a-router", "--role", "client")

    conf = (hub.tmp / "wg" / "dromos-jump.conf").read_text()
    assert "Address    = 10.88.0.1/24" in conf
    assert "ListenPort = 51830" in conf
    assert "Table      = off" in conf
    assert "PostUp     = /usr/local/sbin/jump-forward up %i" in conf
    assert "AllowedIPs = 0.0.0.0/0" in conf
    assert "AllowedIPs = 10.88.0.10/32" in conf
    assert "AllowedIPs = 10.88.0.11/32" in conf
    assert oct((hub.tmp / "wg" / "dromos-jump.conf").stat().st_mode & 0o777) == "0o600"

    state = json.loads((hub.tmp / "config" / "jump.json").read_text())
    assert state["peers"]["b-exit"] == {"role": "exit", "address": "10.88.0.2"}

    env = (hub.tmp / "config" / "jump.env").read_text()
    assert "JUMP_TABLE=188" in env and "JUMP_RULE_PREF=1880" in env


def test_only_one_exit(hub):
    hub("init", "--endpoint", "s.example")
    hub("add", "b1", "--role", "exit")
    proc = hub("add", "b2", "--role", "exit", ok=False)
    assert proc.returncode != 0 and "exit peer already exists" in proc.stderr


def test_client_and_exit_wg_configs(hub):
    hub("init", "--endpoint", "s.example", "--port", "51999")
    hub("add", "b-exit", "--role", "exit")
    hub("add", "a-laptop", "--role", "client")

    client = hub("show", "a-laptop").stdout
    assert "Address    = 10.88.0.10/32" in client
    assert "AllowedIPs = 0.0.0.0/0" in client
    assert "Endpoint   = s.example:51999" in client
    assert "DNS        = 1.1.1.1, 1.0.0.1" in client

    exit_conf = hub("show", "b-exit").stdout
    assert "Address    = 10.88.0.2/24" in exit_conf
    assert "AllowedIPs = 10.88.0.0/24" in exit_conf
    assert "jump-exit-nat up %i 10.88.0.0/24" in exit_conf
    assert "DNS" not in exit_conf


@pytest.mark.parametrize("name,role,addr", [("b-exit", "exit", "10.88.0.2/24"),
                                            ("a-router", "client", "10.88.0.10/32")])
def test_openwrt_env_is_sourceable(hub, name, role, addr):
    hub("init", "--endpoint", "s.example")
    hub("add", name, "--role", role)
    env_text = hub("show", name, "--format", "openwrt").stdout
    envfile = hub.tmp / f"{name}.env"
    envfile.write_text(env_text)
    out = subprocess.run(
        ["sh", "-c", f'. "{envfile}"; echo "$JUMP_ROLE|$JUMP_ADDRESS|$JUMP_DNS|$JUMP_ENDPOINT_PORT"'],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == f"{role}|{addr}|1.1.1.1 1.0.0.1|51830"


def test_remove_frees_address(hub):
    hub("init", "--endpoint", "s.example")
    hub("add", "a1", "--role", "client")
    hub("remove", "a1")
    hub("add", "a2", "--role", "client")
    assert "10.88.0.10" in hub("list").stdout
    assert not (hub.tmp / "secrets" / "peers" / "a1").exists()


def test_keys_are_stable_across_init(hub):
    hub("init", "--endpoint", "s.example")
    pub1 = (hub.tmp / "secrets" / "server.pub").read_text()
    hub("init", "--endpoint", "s2.example")
    assert (hub.tmp / "secrets" / "server.pub").read_text() == pub1


@pytest.mark.parametrize("shell,path", [(s, p) for s, ps in SHELL_SCRIPTS.items() for p in ps])
def test_shell_syntax(shell, path):
    subprocess.run([shell, "-n", str(REPO / path)], check=True)
