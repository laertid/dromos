# Dromos

Two-hop VPN infrastructure with geo-aware routing.

## Overview

Dromos is a self-hosted, two-hop VPN system designed for a small trusted user base (1–10 users). It is built around two VPS nodes:

- **Phebes** — VPS1 with a Russian IP. Client-facing entry node. Terminates user tunnels and decides where each flow exits.
- **Peleon** — VPS2 with an Italian IP. Backbone exit node for non-Russian destinations.

Traffic flow:

```
client ── WG/SS/OVPN ──▶ Phebes (RU)
                         │
                         ├── dst ∈ RU ──▶ exit via Phebes (direct)
                         └── dst ∉ RU ──▶ WG backbone ──▶ Peleon (IT) ──▶ exit
```

This gives users a Russian IP for Russian services (banking, gov, content geo-locked to RU) and a non-Russian IP for everything else, with a single client connection.

## Client-facing protocols

| Priority   | Protocol     | Port      | Notes                                                              |
|------------|--------------|-----------|--------------------------------------------------------------------|
| Primary    | WireGuard    | UDP/51820 | Official apps on Win/Mac/Linux/Android/iOS                         |
| Secondary  | Shadowsocks  | TCP/8388  | For environments that block UDP; proxy or TUN-mode depending on client |
| Fallback   | OpenVPN      | TCP/443   | When everything else is filtered (mimics HTTPS)                    |

## Design principles

1. **No host disruption.** Dromos uses dedicated interface names (`dromos-client`, `dromos-backbone`), dedicated routing tables (`100`, `101`), dedicated nftables tables (`inet dromos`), and dedicated packet marks (`0x42`, `0x43`). It never touches the host default route, the host SSH config, host sysctl outside of the namespaced flags it needs, or any existing nftables tables.
2. **SSH is sacred.** No playbook flushes nftables, runs `ufw reset`, or otherwise risks locking us out. All firewall changes are additive: into Dromos-owned nftables chains, and — when UFW is the active host firewall — only `ufw allow` rules for the WireGuard ports (and SSH is re-asserted alongside them so opening ports can never sever the session).
3. **IPv4 only in v1.** IPv6 is disabled on Dromos interfaces to avoid leaks. v2 may add v6 once we have a v6 geobase strategy.
4. **Idempotent.** Every script and Ansible task can be re-run safely.
5. **Easy to recreate Phebes.** See [docs/rebuild-phebes.md](docs/rebuild-phebes.md). New VPS, run installer, restore creds, update DNS, ~10 min downtime.
6. **No secrets in git.** Generated keys live under `/etc/dromos/secrets/` on the node and in a user-managed backup on the operator's PC.

## Repository layout

```
.
├── README.md                  ← this file
├── docs/
│   ├── architecture.md        decisions, diagrams, packet walk
│   ├── rebuild-phebes.md      runbook for recreating Phebes
│   ├── operations.md          day-2 ops, user mgmt, backups
│   ├── troubleshooting.md     diagnosis recipes
│   └── security.md            threat model, key handling, audit
├── ansible.cfg                roles_path so playbooks find ansible/roles from repo root
├── ansible/
│   ├── ansible.cfg            same, for running from inside ansible/
│   ├── requirements.yml       Galaxy collection deps (ansible.posix)
│   ├── inventory/             sample inventories (no real hosts)
│   │   └── group_vars/all.yml shared paths (config/secrets/cache dirs)
│   ├── playbooks/             site.yml, bootstrap.yml, phebes.yml, peleon.yml
│   └── roles/
│       ├── common/            sysctl, packages, dromos user
│       ├── phebes/            client-facing WG, geo-routing, SS, OVPN
│       └── peleon/            backbone WG, exit NAT
├── templates/                 Jinja2 templates for WG/SS/OVPN/nftables
├── scripts/                   user-add/remove/list, geo-update, backup/restore, healthcheck, diagnose
└── tests/
    ├── lab/                   Docker-compose lab (client→phebes→peleon→dst)
    └── unit/                  Python tests for geobase / config rendering
```

## Quick start

> **Architecture has been approved.** Before deploying to a real Peleon, run the lab tests.

```bash
# 1. Run the lab on your workstation (or joker3 via SSH)
cd tests/lab
./run.sh

# 2. Provision both VPS nodes (Ubuntu 22.04+) in one clean install
ansible-galaxy collection install -r ansible/requirements.yml   # one-time deps

# Build the inventory.  Easiest: run the interactive helper, which asks for the
# two node IPs + the Phebes DNS endpoint and writes ansible/inventory/prod.ini.
# It won't overwrite an existing prod.ini unless you confirm (or pass --force).
scripts/dromos-init-inventory
# (or by hand: cp ansible/inventory/sample.ini ansible/inventory/prod.ini and edit
#  ansible_host for both nodes, dromos_phebes_endpoint, and dromos_peleon_host)

# Run from the repo root: the ansible.cfg there points roles_path at ansible/roles.
ansible-playbook -i ansible/inventory/prod.ini ansible/playbooks/site.yml

# 3. Verify the whole two-hop path from your workstation (reaches both nodes
#    over SSH via the inventory; read-only, prints no secrets). Use this first
#    whenever something is off — it tells you which hop/layer is broken.
scripts/dromos-diagnose -i ansible/inventory/prod.ini

# 4. Add a user (on Phebes). Prints a secret-free summary + the config path.
scripts/dromos-user add alice

# 5. Retrieve the full WG config to hand to alice (contains private + preshared
#    keys, so it is not printed by default):
scripts/dromos-user show alice
#    or: sudo cat /etc/dromos/secrets/clients/alice/alice.conf
#    The config's Endpoint comes from dromos_phebes_endpoint in your inventory.
```

### Clean install: how the backbone keys are handled

The Phebes↔Peleon WireGuard backbone needs each node to know the *other*
node's public key before its config can be written. `site.yml` does this for
you automatically — no manual key copying:

1. `bootstrap.yml` runs first across **both** hosts, generates each node's
   backbone keypair under `/etc/dromos/secrets/` (idempotent; private keys
   never leave the host), and publishes each node's **public** key as an
   Ansible fact.
2. `peleon.yml` and `phebes.yml` then read the peer's public key from those
   facts and render both backbone configs in the same run.

So a clean install is just:

```bash
ansible-playbook -i ansible/inventory/prod.ini ansible/playbooks/site.yml
```

Re-running `site.yml` is safe and idempotent. Existing hosts that already have
keys keep them.

**Running a single node by itself** (e.g. just `phebes.yml`) skips the
cross-host exchange, so pass the peer's backbone public key explicitly:

```bash
# the peer's public key is /etc/dromos/secrets/backbone.pub on that node
ansible-playbook -i ansible/inventory/prod.ini ansible/playbooks/phebes.yml \
  -e dromos_peleon_public_key="$(ssh root@PELEON cat /etc/dromos/secrets/backbone.pub)"

ansible-playbook -i ansible/inventory/prod.ini ansible/playbooks/peleon.yml \
  -e dromos_phebes_backbone_public_key="$(ssh root@PHEBES cat /etc/dromos/secrets/backbone.pub)"
```

## Status

v1.0.0 — initial implementation. Tested in a Docker lab. **Not yet deployed to production Peleon.**

## License

Private. Not for redistribution.
