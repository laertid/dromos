"""dromos-healthcheck fwmark policy-routing rule verification.

Regression: on a correctly-configured Phebes, `ip rule show` prints the routing
table by its rt_tables *alias* (dromos_ru / dromos_foreign), because
dromos-routing installs those aliases in /etc/iproute2/rt_tables. The probe
used to grep for the numeric id (100 / 101) only, so it reported

    [FAIL] ip rule fwmark 0x42 -> 100 missing
    [FAIL] ip rule fwmark 0x43 -> 101 missing

even though the rules were present. These tests drive the real script with a
stubbed `ip` that prints rules in either the numeric or the alias form (and a
matching rt_tables file) and assert the check passes for both.
"""
from __future__ import annotations

import os
import stat
import subprocess
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "dromos-healthcheck"

# Shared stubs that make every non-fwmark Phebes check pass, so any [FAIL] is
# attributable to the fwmark rule check under test. `ip rule show` is driven by
# $DROMOS_IP_RULE_OUTPUT so each test can choose numeric vs. alias form.
_STUBS = {
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
    "ping": "#!/bin/bash\necho \"64 bytes time=1.0 ms\"; exit 0\n",
}


def _ip_stub(rule_output: str) -> str:
    return textwrap.dedent(
        """\
        #!/bin/bash
        case "$*" in
          "link show dromos-client"*) echo "5: dromos-client: <POINTOPOINT> state UNKNOWN"; exit 0 ;;
          "link show dromos-backbone"*) echo "6: dromos-backbone: <POINTOPOINT> state UNKNOWN"; exit 0 ;;
          "link show "*) echo "9: x: state UP"; exit 0 ;;
          "-d link show"*) exit 0 ;;
          "route show default"*) echo "default via 10.0.0.1 dev enx5"; exit 0 ;;
          "rule show"*) printf '%s\\n' "$DROMOS_IP_RULE_OUTPUT"; exit 0 ;;
          "route show table"*) exit 0 ;;
          *) exit 0 ;;
        esac
        """
    )


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run(tmp_path: Path, rule_output: str, rt_tables_body: str | None) -> subprocess.CompletedProcess:
    bind = tmp_path / "bin"
    bind.mkdir()
    for name, body in _STUBS.items():
        _write_exec(bind / name, body)
    _write_exec(bind / "ip", _ip_stub(rule_output))

    rt_tables = tmp_path / "rt_tables"
    if rt_tables_body is not None:
        rt_tables.write_text(rt_tables_body)

    env = dict(os.environ)
    env["PATH"] = f"{bind}:{env['PATH']}"
    env["DROMOS_HAS_CLIENT"] = "1"
    env["DROMOS_ROUTING_ENV"] = "/nonexistent-so-not-sourced"
    env["DROMOS_IP_RULE_OUTPUT"] = rule_output
    env["DROMOS_RT_TABLES"] = str(rt_tables)
    return subprocess.run(
        ["bash", str(SCRIPT), "--role", "phebes"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )


RT_TABLES = textwrap.dedent(
    """\
    255 local
    254 main
    253 default
    100 dromos_ru
    101 dromos_foreign
    """
)

# How iproute2 prints the rules once the rt_tables aliases exist (the real
# Phebes output that used to be misread as missing).
ALIAS_RULES = (
    "32764: from all fwmark 0x43 lookup dromos_foreign\n"
    "32765: from all fwmark 0x42 lookup dromos_ru\n"
    "32766: from all lookup main"
)

# Numeric form (no aliases in rt_tables, or a host that prints ids).
NUMERIC_RULES = (
    "32764: from all fwmark 0x43 lookup 101\n"
    "32765: from all fwmark 0x42 lookup 100\n"
    "32766: from all lookup main"
)


def test_fwmark_rules_pass_with_alias_table_names(tmp_path):
    res = _run(tmp_path, ALIAS_RULES, RT_TABLES)
    out = res.stdout
    assert "[OK]   ip rule fwmark 0x42 -> 100 present" in out, out
    assert "[OK]   ip rule fwmark 0x43 -> 101 present" in out, out
    assert "ip rule fwmark 0x42 -> 100 missing" not in out
    assert "ip rule fwmark 0x43 -> 101 missing" not in out


def test_fwmark_rules_pass_with_numeric_table_ids(tmp_path):
    # Even with no aliases declared, numeric output must still be accepted.
    res = _run(tmp_path, NUMERIC_RULES, rt_tables_body=None)
    out = res.stdout
    assert "[OK]   ip rule fwmark 0x42 -> 100 present" in out, out
    assert "[OK]   ip rule fwmark 0x43 -> 101 present" in out, out


def test_fwmark_rules_fail_when_truly_absent(tmp_path):
    # Sanity: when the rules really are missing, the check still FAILs.
    res = _run(tmp_path, "32766: from all lookup main", RT_TABLES)
    out = res.stdout
    assert "[FAIL] ip rule fwmark 0x42 -> 100 missing" in out, out
    assert "[FAIL] ip rule fwmark 0x43 -> 101 missing" in out, out
