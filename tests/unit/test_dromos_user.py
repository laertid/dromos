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
