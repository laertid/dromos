# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Dromos is a self-hosted two-hop VPN for 1–10 users. Clients connect to **Phebes** (a VPS with a Russian IP) over WireGuard (UDP/51820), Shadowsocks (TCP/8388) or OpenVPN (TCP/443). Phebes sends traffic for Russian destinations straight out of its own uplink. All other traffic goes over a WireGuard backbone (UDP/51821) to **Peleon** (a VPS with an Italian IP), which NATs it out. `jump/` is a separate, independent A→S→B exit tunnel and has its own `jump/CLAUDE.md`.

Start with `README.md` for setup and `docs/architecture.md` for the packet walk, the table of interfaces, tables and marks, and the ADRs. Runbooks are in `docs/`.

## Commands

```bash
pip install -r tests/unit/requirements.txt          # pytest, jinja2, pyyaml
pytest -v tests/unit/                                # all unit tests (what CI runs)
pytest -v tests/unit/test_dromos_user.py -k remove   # single file / test
shellcheck scripts/dromos-routing scripts/dromos-healthcheck ...   # CI lints scripts but ignores failures (|| true)

tests/lab/run.sh        # Docker-compose integration lab (client→phebes→peleon→dst); needs Docker

ansible-galaxy collection install -r ansible/requirements.yml
ansible-playbook -i ansible/inventory/prod.ini ansible/playbooks/site.yml   # run from repo root
scripts/dromos-diagnose -i ansible/inventory/prod.ini                        # read-only end-to-end check
```

Run playbooks from the repo root. The root `ansible.cfg` sets `roles_path = ansible/roles`, and `ansible/ansible.cfg` does the same when you run from inside `ansible/`. `prod.ini` is gitignored, so start from `ansible/inventory/sample.ini` or create it with `scripts/dromos-init-inventory`.

CI (`.github/workflows/ci.yml`) runs the unit tests, shellcheck on the jump scripts (failures there fail CI), and a secret scan that rejects strings like `PRIVATE KEY-----` or `wgPrivateKey` anywhere outside `docs/` and the workflows.

## Architecture

- **Geo routing on Phebes uses fwmark and policy routing**, not VRF or netns (ADR-003). nftables table `inet dromos` looks up the destination in the set `dromos_ru4`. A match gets mark `0x42`, which selects table 100 (direct exit via eth0). Anything else gets `0x43`, which selects table 101 (default via `dromos-backbone` to Peleon). Shadowsocks and OpenVPN (`tun-dromos`) use the same marks, so they follow the same two-hop logic.
- **Persistent state and runtime state are kept separately.** Ansible writes configs, systemd units and the nft ruleset file to disk. The `ip rule` entries, the routes in tables 100/101 and the ipset exist only in the kernel. `scripts/dromos-routing` creates them from wg-quick PostUp, and `scripts/dromos-assert-runtime` (run by a timer every minute) re-adds any that drift away. When you add new kernel state, make sure both paths can recreate it.
- **The playbooks share keys through `site.yml`.** `bootstrap.yml` creates each node's backbone keypair under `/etc/dromos/secrets/` and publishes only the public key as a fact. `phebes.yml` and `peleon.yml` then read the peer's key from those facts. If you run a single-node playbook, pass the peer key with `-e` (see README). Shared paths are in `ansible/inventory/group_vars/all.yml`, not in role defaults, because bootstrap runs with no roles applied.
- **Roles deploy code from the repo root.** They install files from `scripts/` and `templates/` using `{{ playbook_dir }}/../../...`, so those top-level directories are what actually gets deployed. `tests/unit/test_templates_render.py` renders the Jinja2 templates with plain jinja2, outside Ansible.
- **`scripts/dromos-user`** (Python) manages clients on Phebes. Its state is `/etc/dromos/users.json`, secrets live in `/etc/dromos/secrets/clients/<name>/`, and it regenerates `/etc/wireguard/dromos-client.conf` and applies it with `wg syncconf` without bouncing the interface. It reads endpoint and port defaults from `/etc/dromos/client.env` (written by Ansible), and real environment variables take precedence. Like `jump-server`, it reads its paths from env vars (e.g. `DROMOS_CONFIG_DIR`), which is how the tests run it offline.
- **The phebes role has a dead-man timer.** Before it (re)starts the nftables and WireGuard units, it arms a transient `dromos-deadman` timer that tears Dromos down if the deploy cuts SSH, and disarms it once the deploy succeeds. Keep that arm/disarm pairing around any risky network change.

## Hard constraints

- **Don't touch anything on the host that Dromos doesn't own.** Never flush nftables, run `ufw reset`, change UFW default policies, or modify the host default route, sshd config or non-Dromos `ip rule` entries. Firewall changes may only add rules, either in Dromos-owned chains or as `ufw allow` rules with SSH re-asserted next to them.
- **Dromos owns only these resources:** interfaces `dromos-client`/`dromos-backbone`/`tun-dromos`, subnets `10.66.0.0/24` (clients) and `10.77.0.0/30` (backbone), tables 100/101, marks 0x42/0x43, nft table `inet dromos`. Jump uses separate ones (`dromos-jump`, `10.88.0.0/24`, port 51830, rule 1880/table 188) so both can share a VPS. Keep the two sets disjoint.
- **Every script and task must be idempotent** and safe to re-run on a converged host.
- **v1 is IPv4 only.** IPv6 is disabled on Dromos interfaces to prevent leaks.
- **No secrets in git.** Keys live under `/etc/dromos/secrets/` on the nodes. `.gitignore` excludes `prod.ini`, `users.json`, `**/secrets/` and key files.
