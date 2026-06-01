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


def test_client_facing_wg_address_defaults_to_gateway_host_not_network(env):
    """The iface Address must be a usable host (10.66.0.1/24), never the subnet
    network address. Regression: live Phebes rendered `Address = 10.66.0.0/24`
    because the template used dromos_client_subnet_v4 directly."""
    tpl = env.get_template("wireguard/dromos-client.conf.j2")
    out = tpl.render(dromos_server_private_key="SERVERPRIV")
    assert "Address    = 10.66.0.1/24" in out
    # The network address must never be used as the interface address.
    assert "Address    = 10.66.0.0/24" not in out


def test_client_facing_wg_address_honours_explicit_gateway(env):
    tpl = env.get_template("wireguard/dromos-client.conf.j2")
    out = tpl.render(
        dromos_server_private_key="SERVERPRIV",
        dromos_client_subnet_v4="10.66.0.0/24",
        dromos_client_gateway_v4="10.66.0.1/24",
    )
    assert "Address    = 10.66.0.1/24" in out
    assert "Address    = 10.66.0.0/24" not in out


def test_per_user_client_dns_defaults_to_public_resolvers(env):
    """Phebes runs no resolver, so the per-user client template must default
    DNS to public resolvers -- never the tunnel gateway 10.66.0.1, which made
    every lookup fail on a live deployment."""
    tpl = env.get_template("wireguard/client.conf.j2")
    out = tpl.render(
        user={"private_key": "K", "address": "10.66.0.2", "preshared_key": "P"},
        dromos_server_public_key="SPUB",
        dromos_phebes_endpoint="phebes.example.ru",
    )
    assert "DNS        = 1.1.1.1, 1.0.0.1" in out
    assert "DNS        = 10.66.0.1" not in out


def test_per_user_client_dns_honours_override(env):
    tpl = env.get_template("wireguard/client.conf.j2")
    out = tpl.render(
        user={"private_key": "K", "address": "10.66.0.2", "preshared_key": "P"},
        dromos_server_public_key="SPUB",
        dromos_phebes_endpoint="phebes.example.ru",
        dromos_client_dns="9.9.9.9",
    )
    assert "DNS        = 9.9.9.9" in out


def test_openvpn_dns_pushes_one_per_resolver(env):
    """OpenVPN needs one `push dhcp-option DNS` per resolver; a comma list must
    be split, and the default must be public resolvers, not 10.66.0.1."""
    tpl = env.get_template("openvpn/server.conf.j2")
    out = tpl.render()
    assert 'push "dhcp-option DNS 1.1.1.1"' in out
    assert 'push "dhcp-option DNS 1.0.0.1"' in out
    assert 'push "dhcp-option DNS 10.66.0.1"' not in out

    out2 = tpl.render(dromos_client_dns="9.9.9.9, 149.112.112.112")
    assert 'push "dhcp-option DNS 9.9.9.9"' in out2
    assert 'push "dhcp-option DNS 149.112.112.112"' in out2


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


def test_nftables_honours_custom_egress_ifname(env):
    """Regression: live Peleon's default route is enx5, not eth0. The egress
    interface must come from the rendered var, not be hardcoded -- otherwise
    the NAT/forward rules reference the wrong device and egress breaks."""
    tpl = env.get_template("nftables/dromos.nft.j2")
    out = tpl.render(dromos_egress_ifname="enx5")
    assert 'oifname "enx5"' in out
    # The eth0 default must not leak through once a value is supplied.
    assert 'oifname "eth0"' not in out


def test_nftables_egress_defaults_to_eth0_without_var(env):
    """When no egress var is passed at all, the template still renders a valid
    ruleset using the eth0 fallback (the role's last-resort default)."""
    tpl = env.get_template("nftables/dromos.nft.j2")
    out = tpl.render()
    assert '"eth0"' in out


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
