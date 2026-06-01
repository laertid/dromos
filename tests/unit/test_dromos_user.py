"""Unit tests for dromos-user CLI.

We import the script as a module by file path and exercise the pure-function
parts (IP allocation, config rendering).  No subprocess / network needed for
these tests beyond `wg genkey` which we stub via fake binaries on PATH.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "dromos-user"


@pytest.fixture(autouse=True)
def _isolate_environ():
    """load_user_module writes config into os.environ; snapshot and restore so
    one test's DROMOS_* values cannot leak into the next (tests run in-process
    and the module reads these at import time)."""
    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def load_user_module(env: dict) -> object:
    from importlib.machinery import SourceFileLoader
    for k, v in env.items():
        os.environ[k] = v
    loader = SourceFileLoader("dromos_user_mod", str(SCRIPT))
    spec = importlib.util.spec_from_loader("dromos_user_mod", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture
def lab(tmp_path, monkeypatch):
    secrets = tmp_path / "secrets"
    config = tmp_path / "config"
    wg = tmp_path / "wg"
    secrets.mkdir()
    config.mkdir()
    wg.mkdir()
    (secrets / "server.key").write_text("SERVERPRIVKEY\n")
    (secrets / "server.pub").write_text("SERVERPUBKEY\n")

    # Fake wg / wg-quick / wg binaries so the script runs offline.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name, body in {
        "wg": textwrap.dedent("""\
            #!/bin/sh
            case "$1" in
              genkey) echo "PRIV_$$_$RANDOM" ;;
              pubkey) read x; echo "PUB_${x#PRIV_}" ;;
              genpsk) echo "PSK_$$_$RANDOM" ;;
              syncconf) exit 0 ;;
              *) exit 0 ;;
            esac
        """),
        "wg-quick": "#!/bin/sh\ncat \"$2\"\n",
    }.items():
        p = fake_bin / name
        p.write_text(body); p.chmod(0o755)

    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    return {
        "DROMOS_CONFIG_DIR": str(config),
        "DROMOS_SECRETS_DIR": str(secrets),
        "DROMOS_WG_CONF": str(wg / "dromos-client.conf"),
        "DROMOS_CLIENT_SUBNET": "10.66.0.0/24",
        "DROMOS_PHEBES_ENDPOINT": "test.example.com",
    }


def test_add_lists_remove(lab, monkeypatch):
    # Wipe any prior import
    sys.modules.pop("dromos_user_mod", None)
    du = load_user_module(lab)

    class A:  # noqa: D401
        pass
    a = A(); a.name = "alice"
    assert du.cmd_add(a) == 0

    state = json.loads(Path(lab["DROMOS_CONFIG_DIR"], "users.json").read_text())
    assert len(state["users"]) == 1
    alice = state["users"][0]
    assert alice["name"] == "alice"
    assert alice["address"] == "10.66.0.2"  # .1 is server, .2 is first user

    # Second add gets .3
    b = A(); b.name = "bob"
    assert du.cmd_add(b) == 0
    state = json.loads(Path(lab["DROMOS_CONFIG_DIR"], "users.json").read_text())
    assert {u["address"] for u in state["users"]} == {"10.66.0.2", "10.66.0.3"}

    # Idempotency: duplicate add fails
    c = A(); c.name = "alice"
    assert du.cmd_add(c) == 1

    # Remove alice
    r = A(); r.name = "alice"
    assert du.cmd_remove(r) == 0
    state = json.loads(Path(lab["DROMOS_CONFIG_DIR"], "users.json").read_text())
    assert {u["name"] for u in state["users"]} == {"bob"}

    # Remove again fails
    assert du.cmd_remove(r) == 1


def test_server_conf_render_has_peer_blocks(lab):
    sys.modules.pop("dromos_user_mod", None)
    du = load_user_module(lab)

    class A:  # noqa: D401
        pass
    for n in ("u1", "u2", "u3"):
        a = A(); a.name = n
        du.cmd_add(a)

    state = du.load_users()
    conf = du.render_server_conf(state)
    assert conf.count("[Peer]") == 3
    assert "ListenPort = 51820" in conf
    # The server (gateway) address is the first host of the subnet, .1/24 --
    # never the network address, and never handed out to a user.
    assert "Address    = 10.66.0.1/24" in conf
    assert "Address    = 10.66.0.0/24" not in conf
    assert "10.66.0.1" not in {u["address"] for u in state["users"]}
    for n in ("u1", "u2", "u3"):
        assert f"# {n}" in conf


def test_client_conf_has_endpoint(lab):
    sys.modules.pop("dromos_user_mod", None)
    du = load_user_module(lab)
    class A: pass
    a = A(); a.name = "carol"
    du.cmd_add(a)
    state = du.load_users()
    user = state["users"][0]
    conf = du.render_client_conf(user)
    assert "Endpoint   = test.example.com:51820" in conf
    assert "AllowedIPs = 0.0.0.0/0" in conf
    assert "PrivateKey =" in conf


def test_endpoint_and_port_read_from_client_env(lab, monkeypatch):
    """A deployed host with no DROMOS_PHEBES_ENDPOINT env override must pick up
    the endpoint + listen port from /etc/dromos/client.env (written by Ansible),
    not the script's hardcoded placeholder."""
    sys.modules.pop("dromos_user_mod", None)
    # Simulate a real host: the override env vars are NOT set, only the env file.
    monkeypatch.delenv("DROMOS_PHEBES_ENDPOINT", raising=False)
    monkeypatch.delenv("DROMOS_CLIENT_LISTEN_PORT", raising=False)
    monkeypatch.delenv("DROMOS_CLIENT_PORT", raising=False)
    env = {k: v for k, v in lab.items() if k != "DROMOS_PHEBES_ENDPOINT"}
    client_env = Path(env["DROMOS_CONFIG_DIR"]) / "client.env"
    client_env.write_text(
        "# Managed by Ansible\n"
        "DROMOS_PHEBES_ENDPOINT=phebes.local\n"
        "DROMOS_CLIENT_LISTEN_PORT=51999\n"
    )
    env["DROMOS_CLIENT_ENV"] = str(client_env)

    du = load_user_module(env)
    assert du.ENDPOINT == "phebes.local"
    assert du.LISTEN_PORT == 51999

    class A: pass
    a = A(); a.name = "dave"; a.show_config = True
    du.cmd_add(a)
    conf = du.render_client_conf(du.load_users()["users"][0])
    assert "Endpoint   = phebes.local:51999" in conf
    # The placeholder must never leak into a deployed config.
    assert "phebes.example.ru" not in conf


def test_client_dns_defaults_to_public_resolvers(lab, monkeypatch):
    """With no DNS override anywhere, dromos-user must render public resolvers,
    never the tunnel gateway 10.66.0.1 (Phebes runs no resolver)."""
    sys.modules.pop("dromos_user_mod", None)
    monkeypatch.delenv("DROMOS_CLIENT_DNS", raising=False)
    du = load_user_module(lab)
    assert du.DNS == "1.1.1.1, 1.0.0.1"

    class A: pass
    a = A(); a.name = "ivy"
    du.cmd_add(a)
    conf = du.render_client_conf(du.load_users()["users"][0])
    assert "DNS        = 1.1.1.1, 1.0.0.1" in conf
    assert "10.66.0.1" not in conf


def test_client_dns_read_from_client_env(lab, monkeypatch):
    """Ansible writes DROMOS_CLIENT_DNS into client.env; dromos-user must use it."""
    sys.modules.pop("dromos_user_mod", None)
    monkeypatch.delenv("DROMOS_CLIENT_DNS", raising=False)
    client_env = Path(lab["DROMOS_CONFIG_DIR"]) / "client.env"
    client_env.write_text("DROMOS_CLIENT_DNS=8.8.8.8, 8.8.4.4\n")
    env = dict(lab)
    env["DROMOS_CLIENT_ENV"] = str(client_env)
    du = load_user_module(env)
    assert du.DNS == "8.8.8.8, 8.8.4.4"


def test_client_dns_env_overrides_client_env(lab, monkeypatch):
    """An explicit DROMOS_CLIENT_DNS env var wins over the file."""
    sys.modules.pop("dromos_user_mod", None)
    client_env = Path(lab["DROMOS_CONFIG_DIR"]) / "client.env"
    client_env.write_text("DROMOS_CLIENT_DNS=8.8.8.8\n")
    env = dict(lab)
    env["DROMOS_CLIENT_ENV"] = str(client_env)
    env["DROMOS_CLIENT_DNS"] = "9.9.9.9"
    du = load_user_module(env)
    assert du.DNS == "9.9.9.9"


def test_real_env_overrides_client_env(lab, monkeypatch):
    """An explicit DROMOS_PHEBES_ENDPOINT env var still wins over the file."""
    sys.modules.pop("dromos_user_mod", None)
    monkeypatch.delenv("DROMOS_CLIENT_LISTEN_PORT", raising=False)
    client_env = Path(lab["DROMOS_CONFIG_DIR"]) / "client.env"
    client_env.write_text("DROMOS_PHEBES_ENDPOINT=phebes.local\n")
    env = dict(lab)  # keeps DROMOS_PHEBES_ENDPOINT=test.example.com
    env["DROMOS_CLIENT_ENV"] = str(client_env)
    du = load_user_module(env)
    assert du.ENDPOINT == "test.example.com"


def test_add_default_output_has_no_secrets(lab, capsys):
    """Default `add` output must not leak PrivateKey/PresharedKey."""
    sys.modules.pop("dromos_user_mod", None)
    du = load_user_module(lab)

    class A: pass
    a = A(); a.name = "erin"; a.show_config = False
    assert du.cmd_add(a) == 0
    out = capsys.readouterr().out
    assert "PrivateKey" not in out
    assert "PresharedKey" not in out
    # But a useful, non-secret summary is present.
    assert "public_key:" in out
    assert "erin" in out


def test_add_show_config_prints_full_conf(lab, capsys):
    """--show-config opts back into printing the full config."""
    sys.modules.pop("dromos_user_mod", None)
    du = load_user_module(lab)

    class A: pass
    a = A(); a.name = "frank"; a.show_config = True
    assert du.cmd_add(a) == 0
    out = capsys.readouterr().out
    assert "PrivateKey =" in out
    assert "PresharedKey =" in out


def test_show_command_prints_existing_user(lab, capsys):
    sys.modules.pop("dromos_user_mod", None)
    du = load_user_module(lab)

    class A: pass
    a = A(); a.name = "grace"; a.show_config = False
    du.cmd_add(a)
    capsys.readouterr()  # drain

    s = A(); s.name = "grace"
    assert du.cmd_show(s) == 0
    out = capsys.readouterr().out
    assert "PrivateKey =" in out
    assert "Endpoint   = test.example.com:51820" in out

    missing = A(); missing.name = "nope"
    assert du.cmd_show(missing) == 1
