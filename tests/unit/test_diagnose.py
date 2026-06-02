"""scripts/dromos-diagnose -- control-machine, two-hop path diagnostics.

dromos-diagnose runs from the operator's workstation, reads host addresses from
the Ansible inventory, and reaches both nodes over SSH to answer four questions:
Phebes works, Peleon works, the raw network between them works, and WireGuard
between them works. These tests run the *real* script with a stubbed `ssh` (and
a stubbed `date` only where age math matters), driven by environment variables,
so we exercise the actual inventory parser and output/exit logic without any
network. The contract under test:

  * inventory parsing pulls ansible_host for [phebes]/[peleon] and reads
    [dromos:vars] (interface names, Peleon backbone port);
  * the five sections render in order with [OK]/[WARN]/[FAIL] lines;
  * any [FAIL] makes the script exit 1; an all-green run exits 0;
  * a missing tcpdump on Peleon WARNs (skipped) instead of mutating the host;
  * the tool never prints private keys or client configs.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "dromos-diagnose"

INVENTORY = """\
[phebes]
phebes-1 ansible_host=203.0.113.10 ansible_user=root

[peleon]
peleon-1 ansible_host=198.51.100.20 ansible_user=root

[dromos:children]
phebes
peleon

[dromos:vars]
dromos_backbone_ifname  = dromos-backbone
dromos_client_ifname    = dromos-client
dromos_backbone_listen_port_peleon  = 51821
dromos_peleon_host     = 198.51.100.20
"""

# A stub `ssh` that fakes both nodes. It inspects the remote command (the last
# argument) and, where it matters, the target host (user@host, also last-ish),
# returning canned output. Scenario knobs come in via environment variables so
# each test can flip one behaviour without a new stub. `RECENT_HS` is written by
# the harness to a value the script will read back as a fresh handshake epoch.
SSH_STUB = r"""#!/bin/bash
# Collect args; the remote command is the final argument, and the user@host
# token is the first non-option argument.
host=""
cmd=""
for a in "$@"; do
  case "$a" in
    -*) ;;                      # skip ssh options
    *@*) [[ -z "$host" ]] && host="$a" ;;
  esac
done
cmd="${@: -1}"

emit() { printf '%s\n' "$1"; }

# Reachability + sudo, common to both hosts.
case "$cmd" in
  *dromos-ok*) emit "dromos-ok"; exit 0 ;;
  *sudo*-n*true*) emit "sudo-ok"; exit 0 ;;
esac

# systemd is-active: all units active unless DOWN_UNIT matches.
if [[ "$cmd" == *is-active* ]]; then
  if [[ -n "${DOWN_UNIT:-}" && "$cmd" == *"$DOWN_UNIT"* ]]; then exit 3; fi
  exit 0
fi
if [[ "$cmd" == *list-unit-files* ]]; then emit "$cmd"; exit 0; fi

# routing.env egress probe. The real probe classifies the host's
# DROMOS_EGRESS_IF as egress-ok/egress-missing/egress-unset/egress-noenv; the
# stub mirrors those tokens so the script's case-handling is exercised.
if [[ "$cmd" == *routing.env* ]]; then
  if [[ "${EGRESS_NOENV:-0}" == "1" ]]; then emit "egress-noenv"; exit 0; fi
  if [[ "${EGRESS_UNSET:-0}" == "1" ]]; then emit "egress-unset"; exit 0; fi
  emit "egress-ok:${EGRESS_IF:-enx5}"; exit 0
fi

# UFW status / added rules (Peleon path).
if [[ "$cmd" == *"ufw status"* ]]; then
  if [[ "${UFW_ACTIVE:-0}" == "1" ]]; then emit "Status: active"; else emit "Status: inactive"; fi
  exit 0
fi
if [[ "$cmd" == *"ufw show added"* ]]; then
  # The script greps the remote `ufw show added` output for the route-allow
  # rule; mirror that here by exiting nonzero when the rule is absent, so the
  # remote `... | grep -q ...` semantics the script relies on are preserved.
  if [[ "${UFW_ROUTE_ALLOW:-1}" == "1" ]]; then exit 0; else exit 1; fi
fi

# dromos-healthcheck remote run.
if [[ "$cmd" == *dromos-healthcheck* ]]; then
  if [[ "$cmd" == *command\ -v* || "$cmd" == *test\ -x* ]]; then exit 0; fi
  if [[ "${HEALTHCHECK_FAIL:-0}" == "1" ]]; then emit "[FAIL] something is wrong"; exit 1; fi
  emit "[OK]   all good"; exit 0
fi

# WireGuard: latest-handshakes / endpoints / transfer.
if [[ "$cmd" == *latest-handshakes* ]]; then
  if [[ "${NO_HANDSHAKE:-0}" == "1" ]]; then emit "PEERKEY 0"; else emit "PEERKEY ${RECENT_HS:-0}"; fi
  exit 0
fi
if [[ "$cmd" == *endpoints* ]]; then emit "PEERKEY 198.51.100.20:51821"; exit 0; fi
if [[ "$cmd" == *transfer* ]]; then emit "PEERKEY 12345 67890"; exit 0; fi

# Backbone ping.
if [[ "$cmd" == *ping* ]]; then exit 0; fi

# Python UDP send from Phebes.
if [[ "$cmd" == *python3* ]]; then emit "sent-ok"; exit 0; fi

# tcpdump presence on Peleon.
if [[ "$cmd" == *"command -v tcpdump"* ]]; then
  [[ "${PELEON_TCPDUMP:-0}" == "1" ]] && exit 0 || exit 1
fi
if [[ "$cmd" == *tcpdump* ]]; then emit "$!"; exit 0; fi   # capture launcher
if [[ "$cmd" == *dromos-diag-cap* ]]; then
  # An empty capture (NO_INBOUND_UDP=1) simulates a flaky/timed-out tcpdump that
  # caught none of the synthetic probe burst, even on a healthy path.
  if [[ "${NO_INBOUND_UDP:-0}" == "1" ]]; then exit 0; fi
  emit "12:00:00.0 IP a.b.c.d.40000 > e.f.g.h.51821: UDP, length 20"; exit 0
fi

# nft set (geobase) on Phebes.
if [[ "$cmd" == *dromos_ru4* ]]; then
  if [[ "${GEOBASE_EMPTY:-0}" == "1" ]]; then exit 0; fi
  for i in $(seq 1 200); do emit "10.0.$i.0/24"; done
  exit 0
fi

# ip rule fwmark checks.
if [[ "$cmd" == *"ip rule show"* ]]; then
  if [[ "${NO_FWMARK:-0}" == "1" ]]; then exit 1; fi
  emit "fwmark 0x42 lookup 100"; exit 0
fi

exit 0
"""


def _write_stub(bind: Path, name: str, body: str) -> None:
    p = bind / name
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run(tmp_path: Path, *, extra_args=(), **env_overrides):
    bind = tmp_path / "bin"
    bind.mkdir()
    _write_stub(bind, "ssh", SSH_STUB)

    inv = tmp_path / "prod.ini"
    inv.write_text(INVENTORY)

    env = dict(os.environ)
    env["PATH"] = f"{bind}:{env['PATH']}"
    # A handshake epoch the script will read as "recent" (now).
    env["RECENT_HS"] = subprocess.run(
        ["date", "+%s"], capture_output=True, text=True
    ).stdout.strip()
    for k, v in env_overrides.items():
        env[k] = v

    return subprocess.run(
        ["bash", str(SCRIPT), "-i", str(inv), *extra_args],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )


# --- usage / argument handling --------------------------------------------

def test_requires_inventory():
    res = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True)
    assert res.returncode == 2
    assert "inventory is required" in res.stderr


def test_unreadable_inventory_is_usage_error(tmp_path):
    res = subprocess.run(
        ["bash", str(SCRIPT), "-i", str(tmp_path / "nope.ini")],
        capture_output=True, text=True,
    )
    assert res.returncode == 2
    assert "not readable" in res.stderr


# --- inventory parsing ------------------------------------------------------

def test_parses_hosts_from_inventory(tmp_path):
    res = _run(tmp_path, extra_args=("--skip-network",))
    # The header echoes the SSH targets resolved from [phebes]/[peleon].
    assert "root@203.0.113.10" in res.stdout
    assert "root@198.51.100.20" in res.stdout


# --- section structure ------------------------------------------------------

def test_sections_present_and_ordered(tmp_path):
    res = _run(tmp_path)
    out = res.stdout
    order = [
        "== Phebes host",
        "== Peleon host",
        "== Phebes <-> Peleon raw network",
        "== Phebes <-> Peleon WireGuard backbone",
        "== Client path readiness",
        "== Summary",
    ]
    idxs = [out.find(s) for s in order]
    assert all(i >= 0 for i in idxs), out
    assert idxs == sorted(idxs), f"sections out of order: {idxs}"


# --- happy path: everything green, exit 0 ----------------------------------

def test_all_green_exits_zero(tmp_path):
    # UFW active with the route allow present; tcpdump present so the raw path
    # is confirmed; recent handshake; populated geobase; fwmark rules present.
    res = _run(
        tmp_path,
        UFW_ACTIVE="1", UFW_ROUTE_ALLOW="1", PELEON_TCPDUMP="1",
    )
    assert "[FAIL]" not in res.stdout, res.stdout
    assert res.returncode == 0, res.stdout + res.stderr
    assert "All checks passed" in res.stdout


# --- failure propagation ----------------------------------------------------

def test_stale_or_missing_handshake_fails(tmp_path):
    res = _run(tmp_path, NO_HANDSHAKE="1", PELEON_TCPDUMP="1")
    assert "no handshake yet" in res.stdout
    assert res.returncode == 1


def test_empty_geobase_fails(tmp_path):
    res = _run(tmp_path, GEOBASE_EMPTY="1", PELEON_TCPDUMP="1")
    assert "geobase ipset dromos_ru4 nearly empty" in res.stdout
    assert res.returncode == 1


def test_missing_fwmark_rules_fail(tmp_path):
    res = _run(tmp_path, NO_FWMARK="1", PELEON_TCPDUMP="1")
    assert "fwmark 0x42 missing" in res.stdout
    assert "fwmark 0x43 missing" in res.stdout
    assert res.returncode == 1


def test_healthcheck_failure_propagates(tmp_path):
    res = _run(tmp_path, HEALTHCHECK_FAIL="1", PELEON_TCPDUMP="1")
    assert "dromos-healthcheck reported failures" in res.stdout
    # The probe's own [FAIL] lines are surfaced for context.
    assert "something is wrong" in res.stdout
    assert res.returncode == 1


def test_down_service_fails(tmp_path):
    res = _run(tmp_path, DOWN_UNIT="dromos-nft.service", PELEON_TCPDUMP="1")
    assert "service NOT active: dromos-nft.service" in res.stdout
    assert res.returncode == 1


# --- UFW route-allow detection (Peleon) ------------------------------------

def test_ufw_active_without_route_allow_fails(tmp_path):
    res = _run(tmp_path, UFW_ACTIVE="1", UFW_ROUTE_ALLOW="0", PELEON_TCPDUMP="1")
    assert "no route allow for dromos-backbone" in res.stdout
    assert res.returncode == 1


def test_ufw_inactive_is_ok(tmp_path):
    res = _run(tmp_path, UFW_ACTIVE="0", PELEON_TCPDUMP="1")
    assert "UFW inactive" in res.stdout


# --- non-invasive UDP path: tcpdump missing -> WARN, not a host change -----

def test_missing_tcpdump_warns_and_does_not_fail_path(tmp_path):
    res = _run(tmp_path, PELEON_TCPDUMP="0")
    assert "tcpdump not installed on Peleon" in res.stdout
    # The send still succeeds, so this section alone must not FAIL.
    assert "Phebes sent UDP probe" in res.stdout


def test_skip_network_skips_raw_section(tmp_path):
    res = _run(tmp_path, extra_args=("--skip-network",))
    assert "== Phebes <-> Peleon raw network" not in res.stdout


# --- raw UDP capture verdict is deferred to WireGuard health ---------------

def test_empty_capture_warns_when_wireguard_healthy(tmp_path):
    # tcpdump is present but caught nothing, yet the WireGuard backbone is fully
    # healthy (recent handshake, nonzero counters, ping). A flaky synthetic
    # capture must NOT fail the run when real WG data proves the public path.
    res = _run(
        tmp_path,
        PELEON_TCPDUMP="1", NO_INBOUND_UDP="1",
        UFW_ACTIVE="1", UFW_ROUTE_ALLOW="1",
    )
    assert "saw no inbound UDP" in res.stdout, res.stdout
    assert "treating the capture as inconclusive" in res.stdout, res.stdout
    # Downgraded to WARN -> no FAIL from the raw section, run still exits 0.
    assert "[FAIL]" not in res.stdout, res.stdout
    assert res.returncode == 0, res.stdout + res.stderr


def test_empty_capture_fails_when_wireguard_also_unhealthy(tmp_path):
    # Both the synthetic capture AND the WireGuard backbone are unhealthy: with
    # no evidence the public path works, the empty capture stands as a FAIL.
    res = _run(
        tmp_path,
        PELEON_TCPDUMP="1", NO_INBOUND_UDP="1", NO_HANDSHAKE="1",
    )
    assert "saw NO inbound UDP" in res.stdout, res.stdout
    assert "WireGuard backbone is also unhealthy" in res.stdout, res.stdout
    assert res.returncode == 1, res.stdout + res.stderr


# --- routing.env egress classification -------------------------------------

def test_egress_noenv_warns_not_fail(tmp_path):
    res = _run(tmp_path, EGRESS_NOENV="1", PELEON_TCPDUMP="1",
               UFW_ACTIVE="1", UFW_ROUTE_ALLOW="1")
    assert "routing.env unreadable" in res.stdout, res.stdout
    assert "[FAIL]" not in res.stdout, res.stdout
    assert res.returncode == 0, res.stdout + res.stderr


def test_egress_unset_warns_not_fail(tmp_path):
    res = _run(tmp_path, EGRESS_UNSET="1", PELEON_TCPDUMP="1",
               UFW_ACTIVE="1", UFW_ROUTE_ALLOW="1")
    assert "has no DROMOS_EGRESS_IF" in res.stdout, res.stdout
    assert "[FAIL]" not in res.stdout, res.stdout
    assert res.returncode == 0, res.stdout + res.stderr


def test_egress_ok_reports_interface(tmp_path):
    res = _run(tmp_path, EGRESS_IF="eth0", PELEON_TCPDUMP="1",
               UFW_ACTIVE="1", UFW_ROUTE_ALLOW="1")
    assert "egress interface eth0 present" in res.stdout, res.stdout


# --- safety: never prints secrets ------------------------------------------

def test_never_requests_or_prints_private_keys(tmp_path):
    res = _run(tmp_path, UFW_ACTIVE="1", PELEON_TCPDUMP="1")
    combined = res.stdout + res.stderr
    # The tool must not surface key material or client configs anywhere.
    for needle in ("PRIVATE KEY", "PrivateKey", "PresharedKey", ".conf", "wg genkey"):
        assert needle not in combined, f"unexpected {needle!r} in output"
