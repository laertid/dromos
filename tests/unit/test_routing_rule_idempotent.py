"""dromos-routing rule_add_idempotent -- alias-aware policy-rule idempotency.

Regression: `dromos-routing apply client` installs the rt_tables aliases
(100->dromos_ru, 101->dromos_foreign), after which `ip rule show` prints the
rules by *alias* ("lookup dromos_ru") rather than numeric id ("lookup 100").
rule_add_idempotent used to grep only for the numeric id, so on every converge
it failed to recognise the existing rule and ran `ip rule add` again, stacking
duplicate rules.

These tests run the real script with a stubbed `ip` (and a stubbed sysctl/
wg/link so apply_client gets that far) and assert that `ip rule add` is invoked
only when the rule is genuinely absent -- whether the existing rule is printed
in numeric or alias form.
"""
from __future__ import annotations

import os
import stat
import subprocess
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "dromos-routing"

RT_TABLES = textwrap.dedent(
    """\
    255 local
    254 main
    253 default
    100 dromos_ru
    101 dromos_foreign
    """
)

# `ip rule show` already carrying both Dromos rules, in alias form (how a
# correctly-configured host actually prints them once rt_tables exists).
ALIAS_RULES = (
    "0: from all lookup local\n"
    "32764: from all fwmark 0x43 lookup dromos_foreign\n"
    "32765: from all fwmark 0x42 lookup dromos_ru\n"
    "32766: from all lookup main"
)

# Same two rules but printed by numeric id.
NUMERIC_RULES = (
    "0: from all lookup local\n"
    "32764: from all fwmark 0x43 lookup 101\n"
    "32765: from all fwmark 0x42 lookup 100\n"
    "32766: from all lookup main"
)

# Neither Dromos rule present yet.
NO_RULES = (
    "0: from all lookup local\n"
    "32766: from all lookup main"
)


def _ip_stub() -> str:
    # Logs every `ip rule add` invocation to $IP_ADD_LOG so the test can assert
    # whether a (duplicate) add happened. `ip rule show` echoes a fixed ruleset
    # from $DROMOS_IP_RULE_OUTPUT. Everything else (route replace, link show)
    # succeeds quietly.
    return textwrap.dedent(
        """\
        #!/bin/bash
        case "$*" in
          "rule show"*) printf '%s\\n' "$DROMOS_IP_RULE_OUTPUT"; exit 0 ;;
          "rule add"*) printf '%s\\n' "$*" >> "$IP_ADD_LOG"; exit 0 ;;
          "link show "*) exit 0 ;;            # backbone iface "present"
          "route replace"*) exit 0 ;;
          "route show default"*) echo "default via 10.0.0.1 dev enx5"; exit 0 ;;
          *) exit 0 ;;
        esac
        """
    )


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run(tmp_path: Path, rule_output: str) -> tuple[subprocess.CompletedProcess, str]:
    bind = tmp_path / "bin"
    bind.mkdir()
    _write_exec(bind / "ip", _ip_stub())
    # sysctl is called at the end of apply_client; make it a harmless no-op.
    _write_exec(bind / "sysctl", "#!/bin/bash\nexit 0\n")

    rt_tables = tmp_path / "rt_tables"
    rt_tables.write_text(RT_TABLES)
    add_log = tmp_path / "ip_add.log"

    env = dict(os.environ)
    env["PATH"] = f"{bind}:{env['PATH']}"
    env["DROMOS_ROUTING_ENV"] = "/nonexistent-so-not-sourced"
    env["DROMOS_RT_TABLES"] = str(rt_tables)
    env["DROMOS_IP_RULE_OUTPUT"] = rule_output
    env["IP_ADD_LOG"] = str(add_log)

    res = subprocess.run(
        ["bash", str(SCRIPT), "apply", "client"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )
    added = add_log.read_text() if add_log.exists() else ""
    return res, added


def test_no_duplicate_add_when_rules_present_as_alias(tmp_path):
    # The rules are already there in alias form -> apply must add nothing.
    res, added = _run(tmp_path, ALIAS_RULES)
    assert res.returncode == 0, res.stderr
    assert added == "", f"unexpected ip rule add(s):\n{added}"


def test_no_duplicate_add_when_rules_present_as_numeric(tmp_path):
    res, added = _run(tmp_path, NUMERIC_RULES)
    assert res.returncode == 0, res.stderr
    assert added == "", f"unexpected ip rule add(s):\n{added}"


def test_adds_both_rules_when_absent(tmp_path):
    # When the rules are genuinely missing, apply must install both.
    res, added = _run(tmp_path, NO_RULES)
    assert res.returncode == 0, res.stderr
    assert "fwmark 0x42 lookup 100" in added, added
    assert "fwmark 0x43 lookup 101" in added, added
