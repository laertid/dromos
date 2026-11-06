# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Scope: `jump/` is a self-contained subproject of the Dromos repo. It is an A→S→B WireGuard exit tunnel: client A (laptop or OpenWrt router) sends all of its IPv4 traffic through a public hub S to exit B (Linux box or OpenWrt router), so the internet sees B's IP. `README.md` covers the topology, addressing, packet flow and troubleshooting. Each part's folder has a README with details for that part.

## Commands

Run these from the repo root (`..`), the same way CI does (`.github/workflows/ci.yml`):

```bash
pip install -r tests/unit/requirements.txt            # pytest, jinja2, pyyaml
pytest -v tests/unit/test_jump_server.py               # jump tests
pytest -v tests/unit/test_jump_server.py -k only_one_exit   # single test

shellcheck jump/server/jump-forward jump/server/install.sh jump/laptop-a/install.sh \
           jump/laptop-b/install.sh jump/laptop-b/jump-exit-nat
shellcheck -s sh jump/router-a/setup.sh jump/router-b/setup.sh
```

CI fails if jump shellcheck reports anything (unlike the Dromos script lint, which ends in `|| true`). CI also has a secret-scan step that greps for private-key markers (see the `secret-scan` job in `ci.yml` for the regex) anywhere outside `docs/` and the workflows. Keep test fixtures, examples and docs free of those literal strings.

## Architecture

- **`server/jump-server`** (Python 3, stdlib only) is the source of truth. It keeps state in `jump.json` and generates every config the other parts consume: the server `wg` config, `wg-quick` peer configs (`show`), and OpenWrt shell env files (`show --format openwrt`). It applies changes with `wg syncconf` and never bounces the interface. Paths are overridable through `JUMP_CONFIG_DIR`, `JUMP_SECRETS_DIR`, `JUMP_WG_CONF` and `JUMP_FORWARD_BIN`. `JUMP_NO_APPLY=1` only writes files.
- **`server/jump-forward`** runs as PostUp/PreDown on S. It owns the hairpin: rule pref 1880 `iif dromos-jump lookup 188`, a default route via `dromos-jump` in table 188, a FORWARD accept, and `rp_filter=2`. The server config uses `Table = off`, so the exit peer's `0.0.0.0/0` never reaches the main table. S does no NAT.
- **Routing relies on WireGuard cryptokey routing.** Each client owns a `/32`, and the single exit peer (always `10.88.0.2`) owns `0.0.0.0/0`. Because of this there can be only one exit per hub, and `add --role exit` rejects a second one.
- **The OpenWrt env file is a contract.** `render_peer_openwrt()` writes `JUMP_ROLE`, `JUMP_PRIVATE_KEY`, `JUMP_ADDRESS`, `JUMP_SUBNET`, `JUMP_SERVER_PUBLIC_KEY`, `JUMP_ENDPOINT_HOST`/`PORT`, `JUMP_MTU` and `JUMP_DNS` as single-quoted shell assignments. `router-a/setup.sh` and `router-b/setup.sh` source the file and check `JUMP_ROLE`. If you rename or add a variable, update both sides and `test_openwrt_env_is_sourceable`.
- **Exit NAT happens only on B.** On laptop B it is done by `laptop-b/jump-exit-nat`: iptables rules tagged with the comment `dromos-jump-exit`, inserted at the top of FORWARD, with MASQUERADE written as `! -o dromos-jump` and an RFC1918 DROP unless `JUMP_ALLOW_LAN=1`. On router B it comes from the stock `wan` zone masquerade plus `jump → wan` forwarding, with a `jump_block_private` REJECT rule for the same private ranges (skipped with `JUMP_ALLOW_LAN=1`).
- **Router A** policy-routes only traffic that enters from `lan` (rule 1880, table 188), so the router's own handshake to S goes out over WAN with no loop. It applies a kill switch (`JUMP_KILL_SWITCH`) and disables LAN IPv6 (`JUMP_DISABLE_IPV6`). It records the original settings in `/etc/dromos-jump.state`, so `--remove` can restore them.

## Constraints when editing

- **It shares a host with Dromos (Phebes/Peleon).** Jump must keep to its own resources: interface `dromos-jump` (`jump` on OpenWrt), UDP 51830, `10.88.0.0/24`, rule pref 1880 and table 188. Never touch Dromos's 51820/51821, `10.66.0.0/24`, `10.77.0.0/30`, tables 100/101, fwmarks 0x42/0x43 or its nftables. Firewall changes may only add rules: never reset UFW or change default policies.
- **These values are duplicated across files** (Python, bash, sh and READMEs): the addressing constants above, MTU 1380, keepalive 25 and the default DNS `1.1.1.1, 1.0.0.1`. If you change one, grep the whole `jump/` tree.
- **Each script uses a fixed shell dialect.** Server and laptop scripts are bash. `router-*/setup.sh` must stay POSIX `sh` for BusyBox ash on OpenWrt 24.10/25.12, using UCI and fw4, and must support both `apk` and `opkg`.
- **Every script must stay idempotent and re-runnable.** Every install or setup script also needs a working `--remove`, and the `up`/`down` helpers must remove exactly what they add.
- **Tunnels carry IPv4 only**, and IPv6 is deliberately disabled or blackholed.
- **Tests run `jump-server` as a subprocess.** They put a fake `wg` binary on `PATH` and set `JUMP_NO_APPLY=1` (see the `hub` fixture), so they need no root and no WireGuard.
