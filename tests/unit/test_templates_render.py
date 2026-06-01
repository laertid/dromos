"""Templates should render with realistic vars and produce valid-looking config."""
from __future__ import annotations

from pathlib import Path

import jinja2
import pytest

REPO = Path(__file__).resolve().parents[2]
TPL_DIR = REPO / "templates"


@pytest.fixture
def env():
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TPL_DIR)),
        keep_trailing_newline=True,
    )


def test_client_facing_wg_renders(env):
    tpl = env.get_template("wireguard/dromos-client.conf.j2")
    out = tpl.render(
        dromos_server_private_key="SERVERPRIV",
        dromos_users=[
            {"name": "alice", "public_key": "ALICEPUB", "address": "10.66.0.2", "preshared_key": "PSK1"},
            {"name": "bob",   "public_key": "BOBPUB",   "address": "10.66.0.3", "preshared_key": "PSK2"},
        ],
    )
    assert "[Interface]" in out
    assert "ListenPort = 51820" in out
    assert out.count("[Peer]") == 2
    assert "AllowedIPs = 10.66.0.2/32" in out
    assert "AllowedIPs = 10.66.0.3/32" in out


def test_backbone_phebes_renders(env):
    tpl = env.get_template("wireguard/dromos-backbone-phebes.conf.j2")
    out = tpl.render(
        dromos_backbone_phebes_private_key="BBPRIV",
        dromos_peleon_public_key="PELEONPUB",
        dromos_peleon_host="203.0.113.5",
    )
    assert "Endpoint   = 203.0.113.5:51821" in out
    assert "AllowedIPs = 10.77.0.2/32, 0.0.0.0/0" in out


def test_backbone_peleon_renders(env):
    tpl = env.get_template("wireguard/dromos-backbone-peleon.conf.j2")
    out = tpl.render(
        dromos_backbone_peleon_private_key="BBPRIV",
        dromos_phebes_backbone_public_key="PHEBESPUB",
    )
    assert "[Peer]" in out
    assert "PublicKey  = PHEBESPUB" in out
    assert "AllowedIPs = 10.77.0.1/32, 10.66.0.0/24" in out
    # Regression: an empty Phebes key used to render `PublicKey  = ` (trailing
    # space, no value), which wg-quick rejects with "Line unrecognized:
    # PublicKey=". The role now validates the key is non-empty before rendering;
    # this guards the template contract the validation relies on.
    assert "PublicKey  = \n" not in out
    assert "PublicKey  =  " not in out


def test_nftables_renders(env):
    tpl = env.get_template("nftables/dromos.nft.j2")
    out = tpl.render()
    assert "table inet dromos" in out
    assert "set dromos_ru4" in out
    assert "meta mark set 0x42" in out
    assert "meta mark set 0x43" in out
    assert "type nat hook postrouting" in out


def test_shadowsocks_renders_no_password_leak(env):
    tpl = env.get_template("shadowsocks/config.json.j2")
    out = tpl.render(dromos_ss_password="VERY_SECRET")
    import json as _json
    parsed = _json.loads(out)
    assert parsed["method"] == "chacha20-ietf-poly1305"
    assert parsed["server_port"] == 8388
    # We don't object to the literal here -- only that the renderer didn't
    # mangle special chars or drop the field.
    assert parsed["password"] == "VERY_SECRET"
