"""scripts/dromos-assert-runtime -- Phebes runtime-state self-heal.

Dromos policy rules (fwmark 0x42/0x43 -> tables 100/101), the per-table default
routes, and the geobase nft set are *kernel* runtime state. They are re-created
by wg-quick PostUp hooks only when an interface actually restarts, so on a
converged host they can silently drift away while WireGuard stays up (observed
live: `ip rule show` lost the Dromos rules, healthcheck red, fixed by a manual
`dromos-routing apply client`). dromos-assert-runtime is the periodic self-heal
for that gap, run by dromos-routing-assert.timer.

These tests run the real script with stubbed `ip`, `nft`, `systemctl`,
`dromos-routing`, and `dromos-geo-update`. The contract under test:

  * when all runtime state is present, the script repairs nothing and exits 0;
  * when the fwmark rules are missing, it runs `dromos-routing apply client`;
  * when the nft table is gone, it starts dromos-nft.service;
  * when the geobase set is low and a cache exists, it reloads from cache
    (never running a network geo update); with no cache it runs geo-update;
  * when the backbone table has no default route, it runs apply backbone;
  * it is Phebes-only: a host without dromos-client does nothing and exits 0;
  * it never restarts WireGuard, never flushes the firewall, never touches the
    host default route.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "dromos-assert-runtime"


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# Fake `ip`. Behaviour is driven by env knobs so each test flips one thing:
#   HAVE_CLIENT/HAVE_BACKBONE  -> `ip link show <if>` success (iface present)
#   HAVE_FWMARK                -> `ip rule show` includes both Dromos rules
#   HAVE_BACKBONE_ROUTE        -> `ip route show table 101` has a default
# Every other `ip` call (the apply path is delegated to the dromos-routing stub)
# succeeds quietly. Calls are logged so we can assert what was/ wasn't touched.
IP_STUB = r"""#!/bin/bash
printf '%s\n' "$*" >> "$IP_LOG"
case "$*" in
  "link show dromos-client"*)   [[ "${HAVE_CLIENT:-1}" == "1" ]] && exit 0 || exit 1 ;;
  "link show dromos-backbone"*) [[ "${HAVE_BACKBONE:-1}" == "1" ]] && exit 0 || exit 1 ;;
  "link show "*) exit 1 ;;
  "rule show"*)
    # Rules are present if they started present OR after apply client ran
    # (the dromos-routing stub writes FWMARK_FLAG then).
    if [[ "${HAVE_FWMARK:-1}" == "1" ]] || [[ -s "$FWMARK_FLAG" ]]; then
      printf '32765: from all fwmark 0x42 lookup dromos_ru\n'
      printf '32764: from all fwmark 0x43 lookup dromos_foreign\n'
    fi
    exit 0 ;;
  "route show table 101"*)
    if [[ "${HAVE_BACKBONE_ROUTE:-1}" == "1" ]] || [[ -s "$BACKBONE_ROUTE_FLAG" ]]; then
      echo "default dev dromos-backbone"
    fi
    exit 0 ;;
  "route show default"*) echo "default via 10.0.0.1 dev eth0"; exit 0 ;;
  *) exit 0 ;;
esac
"""

# Fake `nft`. NFT_TABLE_PRESENT controls whether `nft list table inet dromos`
# succeeds. RU_COUNT controls how many CIDRs `nft list set ...` prints. `nft -f`
# (cache reload) is logged and succeeds.
NFT_STUB = r"""#!/bin/bash
printf '%s\n' "$*" >> "$NFT_LOG"
case "$1" in
  list)
    if [[ "$2" == "table" ]]; then
      # The table is present if it started present, OR once dromos-nft.service
      # has been started (the systemctl stub writes NFT_TABLE_FLAG then).
      if [[ "${NFT_TABLE_PRESENT:-1}" == "1" ]] || [[ -s "$NFT_TABLE_FLAG" ]]; then
        exit 0
      fi
      exit 1
    fi
    if [[ "$2" == "set" ]]; then
      # After a cache reload (`nft -f`, which writes RELOADED_FLAG) or a geo
      # update, the set is full again regardless of the initial RU_COUNT.
      if [[ -s "$RELOADED_FLAG" ]] || [[ -s "$GEO_RAN_FLAG" ]]; then n=200; else n=${RU_COUNT:-200}; fi
      for ((i=0;i<n;i++)); do echo "10.0.$((i%250)).0/24"; done
      exit 0
    fi
    ;;
  -f) echo 1 > "$RELOADED_FLAG"; exit 0 ;;
esac
exit 0
"""

SYSTEMCTL_STUB = r"""#!/bin/bash
printf '%s\n' "$*" >> "$SYSTEMCTL_LOG"
# Starting dromos-nft "creates" the table for the post-check.
if [[ "$1" == "start" && "$*" == *dromos-nft* ]]; then
  echo 1 > "$NFT_TABLE_FLAG"
fi
exit 0
"""

ROUTING_STUB = r"""#!/bin/bash
printf '%s\n' "$*" >> "$ROUTING_LOG"
# Applying client "restores" the fwmark rules; applying backbone "restores" the
# table-101 default. The ip stub reads these flags to flip its later answers.
[[ "$*" == "apply client" ]]   && echo 1 > "$FWMARK_FLAG"
[[ "$*" == "apply backbone" ]] && echo 1 > "$BACKBONE_ROUTE_FLAG"
exit 0
"""

GEO_STUB = r"""#!/bin/bash
printf '%s\n' "$*" >> "$GEO_LOG"
echo 1 > "$GEO_RAN_FLAG"
exit 0
"""


def _run(tmp_path: Path, **env_overrides):
    bind = tmp_path / "bin"
    bind.mkdir()
    sbin = tmp_path / "sbin"
    sbin.mkdir()

    logs = {k: tmp_path / f"{k.lower()}" for k in
            ("IP_LOG", "NFT_LOG", "SYSTEMCTL_LOG", "ROUTING_LOG", "GEO_LOG")}
    flags = {k: tmp_path / f"{k.lower()}" for k in
             ("NFT_TABLE_FLAG", "FWMARK_FLAG", "BACKBONE_ROUTE_FLAG",
              "GEO_RAN_FLAG", "RELOADED_FLAG")}

    _write_exec(bind / "ip", IP_STUB)
    _write_exec(bind / "nft", NFT_STUB)
    _write_exec(bind / "systemctl", SYSTEMCTL_STUB)
    _write_exec(sbin / "dromos-routing", ROUTING_STUB)
    _write_exec(sbin / "dromos-geo-update", GEO_STUB)

    rt_tables = tmp_path / "rt_tables"
    rt_tables.write_text("100 dromos_ru\n101 dromos_foreign\n")

    env = dict(os.environ)
    env["PATH"] = f"{bind}:{env['PATH']}"
    env["DROMOS_ROUTING_ENV"] = "/nonexistent-not-sourced"
    env["DROMOS_ROLE"] = "phebes"
    env["DROMOS_RT_TABLES"] = str(rt_tables)
    env["DROMOS_ROUTING"] = str(sbin / "dromos-routing")
    env["DROMOS_GEO_UPDATE"] = str(sbin / "dromos-geo-update")
    env["DROMOS_CACHE_DIR"] = str(tmp_path / "cache")
    for k, p in {**logs, **flags}.items():
        env[k] = str(p)
    for k, v in env_overrides.items():
        env[k] = v

    res = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )
    read = lambda p: p.read_text() if p.exists() else ""
    return res, {k: read(v) for k, v in {**logs, **flags}.items()}


# --- happy path: nothing to repair -----------------------------------------

def test_all_present_repairs_nothing(tmp_path):
    res, out = _run(tmp_path)  # all HAVE_* default to present
    assert res.returncode == 0, res.stdout + res.stderr
    assert out["ROUTING_LOG"] == "", f"unexpected dromos-routing call:\n{out['ROUTING_LOG']}"
    assert out["GEO_LOG"] == "", f"unexpected geo-update call:\n{out['GEO_LOG']}"
    # Never starts/restarts any unit when nothing drifted.
    assert out["SYSTEMCTL_LOG"] == "", out["SYSTEMCTL_LOG"]
    assert "runtime state OK" in res.stdout


# --- fwmark policy rules drifted -------------------------------------------

def test_missing_fwmark_runs_apply_client(tmp_path):
    res, out = _run(tmp_path, HAVE_FWMARK="0")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "apply client" in out["ROUTING_LOG"], out["ROUTING_LOG"]
    # It must NOT also run apply backbone (that table was fine).
    assert "apply backbone" not in out["ROUTING_LOG"], out["ROUTING_LOG"]


# --- nft table gone ---------------------------------------------------------

def test_missing_nft_table_starts_service(tmp_path):
    res, out = _run(tmp_path, NFT_TABLE_PRESENT="0")
    # systemctl start dromos-nft.service must have been invoked.
    assert "start dromos-nft.service" in out["SYSTEMCTL_LOG"], out["SYSTEMCTL_LOG"]


# --- geobase low ------------------------------------------------------------

def test_low_geobase_reloads_from_cache_not_network(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "ipset-ru4.cache").write_text(
        "\n".join(f"10.1.{i}.0/24" for i in range(200)) + "\n"
    )
    res, out = _run(tmp_path, RU_COUNT="3")
    # Reloaded from cache via `nft -f`, and the network geo update did NOT run.
    assert "-f" in out["NFT_LOG"], out["NFT_LOG"]
    assert out["GEO_RAN_FLAG"] == "", "geo-update ran despite a usable cache"


def test_low_geobase_no_cache_runs_geo_update(tmp_path):
    res, out = _run(tmp_path, RU_COUNT="3")  # no cache dir contents
    assert out["GEO_RAN_FLAG"].strip() == "1", "geo-update did not run without a cache"


# --- backbone table drifted -------------------------------------------------

def test_missing_backbone_route_runs_apply_backbone(tmp_path):
    res, out = _run(tmp_path, HAVE_BACKBONE_ROUTE="0")
    assert "apply backbone" in out["ROUTING_LOG"], out["ROUTING_LOG"]


# --- role gating ------------------------------------------------------------

def test_non_phebes_role_does_nothing(tmp_path):
    res, out = _run(tmp_path, DROMOS_ROLE="peleon")
    assert res.returncode == 0, res.stdout + res.stderr
    assert out["ROUTING_LOG"] == ""
    assert out["GEO_LOG"] == ""
    assert "Phebes-only" in res.stdout


def test_autodetects_phebes_from_client_iface(tmp_path):
    # No role in env; dromos-client present -> treated as Phebes and asserted.
    res, out = _run(tmp_path, DROMOS_ROLE="", HAVE_FWMARK="0")
    assert "apply client" in out["ROUTING_LOG"], out["ROUTING_LOG"]


def test_autodetects_peleon_when_no_client_iface(tmp_path):
    res, out = _run(tmp_path, DROMOS_ROLE="", HAVE_CLIENT="0")
    assert out["ROUTING_LOG"] == "", out["ROUTING_LOG"]
    assert "Phebes-only" in res.stdout


# --- safety: never restarts WireGuard, never flushes firewall --------------

def test_never_restarts_wireguard_or_flushes(tmp_path):
    # Drift everything so the script does its most aggressive repair, then assert
    # it still never touched WireGuard units or the host default route / firewall.
    res, out = _run(
        tmp_path, HAVE_FWMARK="0", HAVE_BACKBONE_ROUTE="0",
        NFT_TABLE_PRESENT="0", RU_COUNT="3",
    )
    combined = out["SYSTEMCTL_LOG"] + out["IP_LOG"] + out["NFT_LOG"]
    assert "wg-quick" not in combined, combined
    assert "restart" not in out["SYSTEMCTL_LOG"], out["SYSTEMCTL_LOG"]
    # Only `nft list` / `nft -f` (cache reload) are allowed; never flush table.
    assert "flush table" not in out["NFT_LOG"], out["NFT_LOG"]
    # Never deletes the table or rewrites the whole ruleset from here.
    assert "delete table" not in out["NFT_LOG"], out["NFT_LOG"]
    # Never touches the host default route (route replace on the main table).
    assert "route replace default" not in out["IP_LOG"], out["IP_LOG"]
