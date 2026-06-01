"""The common firewall role must emit a UFW *route* (forward) allow for the
backbone -> egress path, and the Peleon role must wire the right interfaces in.

Regression: a live Peleon ran UFW with a default-deny routed policy. Allowing
the backbone UDP port let the tunnel handshake, but UFW still dropped
decapsulated client traffic on the FORWARD path -- Phebes sent MB to Peleon and
got almost nothing back. The fix adds `ufw route allow in on dromos-backbone
out on <egress>`. These tests assert that contract from the task definitions so
it can't silently regress, and that Phebes (no forwarding) does NOT request it.
"""
from __future__ import annotations

from pathlib import Path

import jinja2
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
FIREWALL = REPO / "ansible" / "roles" / "common" / "tasks" / "firewall.yml"
PELEON = REPO / "ansible" / "roles" / "peleon" / "tasks" / "main.yml"
PHEBES = REPO / "ansible" / "roles" / "phebes" / "tasks" / "main.yml"


def _tasks(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text())


def _find(tasks: list[dict], needle: str) -> dict:
    for t in tasks:
        if needle in (t.get("name") or ""):
            return t
    raise AssertionError(f"no task matching {needle!r}")


def test_firewall_has_route_allow_task():
    route = _find(_tasks(FIREWALL), "Allow Dromos forwarding")
    cmd = route["ansible.builtin.command"]["cmd"]
    # The rule must be a routed (forward) allow, in on the backbone, out on the
    # egress, tagged so it can be matched/audited later.
    assert "ufw route allow" in cmd
    assert "in on {{ dromos_firewall_route_in_ifname }}" in cmd
    assert "out on {{ dromos_firewall_route_out_ifname }}" in cmd
    assert "dromos: backbone to internet" in cmd


def test_route_allow_is_guarded_and_idempotent_and_safe():
    route = _find(_tasks(FIREWALL), "Allow Dromos forwarding")
    conds = route["when"]
    # Only runs when UFW is active AND both interfaces are supplied (so Phebes,
    # which passes neither, skips it). Never resets UFW or touches the default
    # policy -- it's a pure additive allow.
    assert any("_ufw_active" in c for c in conds)
    assert any("dromos_firewall_route_in_ifname" in c for c in conds)
    assert any("dromos_firewall_route_out_ifname" in c for c in conds)
    # Safety contract: no task in the firewall role may reset/disable UFW or
    # rewrite the default policy. Check the executed commands, not comments.
    for t in _tasks(FIREWALL):
        cmd = (t.get("ansible.builtin.command") or {})
        cmd_str = cmd.get("cmd", "") if isinstance(cmd, dict) else str(cmd)
        assert "ufw reset" not in cmd_str
        assert "ufw disable" not in cmd_str
        assert "default deny" not in cmd_str


def test_route_allow_command_renders_concrete_rule():
    """Render the command template the way Ansible would for a Peleon whose
    egress is enx5, and assert it matches the rule applied live."""
    route = _find(_tasks(FIREWALL), "Allow Dromos forwarding")
    cmd = route["ansible.builtin.command"]["cmd"]
    rendered = jinja2.Environment().from_string(cmd).render(
        dromos_firewall_route_in_ifname="dromos-backbone",
        dromos_firewall_route_out_ifname="enx5",
    )
    assert " ".join(rendered.split()) == (
        "ufw route allow in on dromos-backbone out on enx5 "
        "comment 'dromos: backbone to internet'"
    )


def test_peleon_wires_backbone_and_egress_into_firewall():
    inc = _find(_tasks(PELEON), "Allow required ports through UFW (Peleon)")
    v = inc["vars"]
    assert "dromos_backbone_ifname" in v["dromos_firewall_route_in_ifname"]
    assert "dromos_egress_ifname" in v["dromos_firewall_route_out_ifname"]


def test_phebes_does_not_request_route_allow():
    inc = _find(_tasks(PHEBES), "Allow required ports through UFW (Phebes)")
    v = inc.get("vars", {})
    # Phebes is not an exit node; it must not pass the route-forward interfaces.
    assert "dromos_firewall_route_in_ifname" not in v
    assert "dromos_firewall_route_out_ifname" not in v
