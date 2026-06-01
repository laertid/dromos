"""dromos-healthcheck must be role-aware.

Regression: on Peleon (exit node, no dromos-client, no RU ipset, no fwmark
policy routing) the probe used to emit misleading [FAIL]s for Phebes-only
resources. These tests run the real script against a stubbed command
environment and assert that Peleon mode does NOT probe client/ipset/ip-rule
resources, while Phebes mode still does.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "dromos-healthcheck"

# A stub `ip`/`wg`/`nft`/`ping`/`date` set that makes every backbone+nft+egress
# check pass, so any [FAIL] in the output is attributable to a role-specific
# check the script should NOT have run for that role.
STUBS = {
    "ip": r"""#!/bin/bash
case "$*" in
  "link show dromos-client"*) [[ "$DROMOS_HAS_CLIENT" == "1" ]] && { echo "5: dromos-client: <POINTOPOINT> state UNKNOWN"; exit 0; } || exit 1 ;;
  "link show dromos-backbone"*) echo "6: dromos-backbone: <POINTOPOINT> state UNKNOWN"; exit 0 ;;
  "link show "*) echo "9: x: state UP"; exit 0 ;;
  "-d link show"*) exit 0 ;;
  "route show default"*) echo "default via 10.0.0.1 dev enx5"; exit 0 ;;
  "rule show"*) echo "32766: from all lookup main"; exit 0 ;;
  "route show table"*) exit 0 ;;
  *) exit 0 ;;
esac
""",
    "wg": r"""#!/bin/bash
case "$*" in
  *"latest-handshakes"*) echo "PEERKEY $(date +%s)"; exit 0 ;;
  *"peers"*) echo "PEERKEY"; exit 0 ;;
  *) exit 0 ;;
esac
""",
    "nft": r"""#!/bin/bash
case "$*" in
  "list table inet dromos") printf '    chain a\n    chain b\n    chain c\n    chain d\n'; exit 0 ;;
  "list set inet dromos dromos_ru4") for i in $(seq 1 200); do echo "10.0.$i.0/24"; done; exit 0 ;;
  *) exit 0 ;;
esac
""",
    "ping": r"""#!/bin/bash
echo "64 bytes time=1.0 ms"; exit 0
""",
}


def _make_stub_dir(tmp_path: Path) -> Path:
    bind = tmp_path / "bin"
    bind.mkdir()
    for name, body in STUBS.items():
        p = bind / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bind


def _run(tmp_path: Path, *, role: str, has_client: bool):
    bind = _make_stub_dir(tmp_path)
    env = dict(os.environ)
    # Stubs first, then keep real /bin etc. for bash builtins like date/seq/awk.
    env["PATH"] = f"{bind}:{env['PATH']}"
    env["DROMOS_HAS_CLIENT"] = "1" if has_client else "0"
    env["DROMOS_ROUTING_ENV"] = "/nonexistent-so-not-sourced"
    return subprocess.run(
        ["bash", str(SCRIPT), "--role", role],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )


def test_peleon_role_skips_phebes_only_checks(tmp_path):
    res = _run(tmp_path, role="peleon", has_client=False)
    out = res.stdout
    assert "role: peleon" in out
    # None of the Phebes-only probes should appear at all.
    assert "dromos-client" not in out
    assert "ipset" not in out
    assert "ip rule fwmark" not in out
    assert "geobase" not in out
    # And with everything stubbed green, Peleon must exit 0 (no false FAILs).
    assert "[FAIL]" not in out, out
    assert res.returncode == 0, res.stdout + res.stderr


def test_phebes_role_runs_full_checks(tmp_path):
    res = _run(tmp_path, role="phebes", has_client=True)
    out = res.stdout
    assert "role: phebes" in out
    assert "dromos-client up" in out
    # Phebes-only probes that Peleon mode skips are present here.
    assert "[OK]   ipset dromos_ru4 entries: 200" in out
    # The fwmark policy-routing rules are probed (the stub has none, so these
    # show as FAIL -- the point is that Phebes mode *runs* the check at all).
    assert "ip rule fwmark" in out


def test_role_autodetect_without_client_is_peleon(tmp_path):
    # No --role, no client iface -> auto-detect peleon, skip Phebes checks.
    bind = _make_stub_dir(tmp_path)
    env = dict(os.environ)
    env["PATH"] = f"{bind}:{env['PATH']}"
    env["DROMOS_HAS_CLIENT"] = "0"
    env["DROMOS_ROUTING_ENV"] = "/nonexistent-so-not-sourced"
    res = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )
    assert "role: peleon" in res.stdout
    assert "dromos-client" not in res.stdout
