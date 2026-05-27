"""Sanity tests for the geo-update script.

We don't need network here.  We only validate the script's CIDR-filter regex
and refuse-empty behaviour by spinning it up with a mocked GEO_URL pointing
at a local file:// served path.
"""
from __future__ import annotations

import os
import re
import subprocess
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "dromos-geo-update"


CIDR_RE = re.compile(r"^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]+$")


def test_cidr_regex_filters_garbage():
    good = ["1.2.3.4/24", "10.0.0.0/8", "192.168.1.0/30"]
    bad = ["", "#comment", "1.2.3", "::1/64", "garbage", "1.2.3.4.5/32"]
    for g in good:
        assert CIDR_RE.match(g), g
    for b in bad:
        assert not CIDR_RE.match(b), b


def test_script_refuses_empty_download(tmp_path, monkeypatch):
    # Stage an empty source file.
    src = tmp_path / "empty.zone"; src.write_text("")
    # Fake the nft binary so we don't need root or nft installed.
    fake = tmp_path / "bin"; fake.mkdir()
    (fake / "nft").write_text("#!/bin/sh\nexit 0\n"); (fake / "nft").chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake}:{env['PATH']}"
    env["GEO_URL"] = src.as_uri()
    env["DROMOS_CACHE_DIR"] = str(tmp_path / "cache")
    env["DROMOS_MIN_CIDRS"] = "10"

    res = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env, capture_output=True, text=True,
    )
    assert res.returncode != 0, f"expected failure, got stdout={res.stdout} stderr={res.stderr}"
    assert "only 0 CIDRs" in res.stderr or "ERROR" in res.stderr


def test_script_accepts_valid_zone(tmp_path):
    cidrs = "\n".join(f"10.{i}.0.0/16" for i in range(120))
    src = tmp_path / "ru.zone"; src.write_text(cidrs + "\n# comment\nGARBAGE\n")
    fake = tmp_path / "bin"; fake.mkdir()
    nft = fake / "nft"
    nft.write_text(textwrap.dedent("""\
        #!/bin/sh
        # log what was sent and succeed
        if [ "$1" = "-f" ]; then
          cp "$2" /tmp/nft-last-batch.$$
        fi
        exit 0
    """))
    nft.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake}:{env['PATH']}"
    env["GEO_URL"] = src.as_uri()
    env["DROMOS_CACHE_DIR"] = str(tmp_path / "cache")
    env["DROMOS_MIN_CIDRS"] = "10"

    res = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env, capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    cache = Path(env["DROMOS_CACHE_DIR"]) / "ipset-ru4.cache"
    assert cache.exists()
    assert cache.read_text().count("\n") == 120
