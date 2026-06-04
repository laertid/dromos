# Operations

Day-2 operations for Dromos.

## User management

All commands run on Phebes as root (or via `sudo`). They are idempotent.

### Add a user

```bash
dromos-user add alice
```

Effect:

- Generates a fresh WG keypair under `/etc/dromos/secrets/clients/alice/`.
- Allocates the next free `10.66.0.X` IP.
- Appends an `[Peer]` block to `dromos-client` and reloads.
- Writes the user-facing config to `/etc/dromos/secrets/clients/alice/alice.conf` (mode `0600`).
- Prints a **secret-free summary** to stdout (name, IP, public key, endpoint, config path).

The client `Endpoint` is taken from `/etc/dromos/client.env` (written by Ansible
from the inventory `dromos_phebes_endpoint` / `dromos_client_listen_port`), so a
freshly deployed host generates configs pointed at the right public endpoint.

> **The private key and preshared key are no longer printed by default.** This
> keeps them out of shell history, terminal scrollback, and orchestration/CI
> logs. To view or hand off the full config, use one of:
>
> ```bash
> dromos-user show alice           # print the full config of an existing user
> dromos-user add alice --show-config   # print full config when creating
> sudo cat /etc/dromos/secrets/clients/alice/alice.conf
> ```

After running, **copy `/etc/dromos/secrets/clients/alice/alice.conf` off the server** to your operator backup directory. The server keeps it for convenience but the operator backup is canonical.

### Remove a user

```bash
dromos-user remove alice
```

Removes the peer from the running interface, removes the `[Peer]` block, and zeroes the private key file before deleting. Connection drops within ~1s.

### List users

```bash
dromos-user list
```

Prints name, allocated IP, last handshake age, bytes rx/tx.

## Backups

```bash
# on your operator workstation
scripts/dromos-backup pull root@phebes.example.ru ~/dromos-backup/
```

Pulls (over SSH):

- `/etc/dromos/secrets/server.{key,pub}`
- `/etc/dromos/secrets/clients/*/` (each user's `*.conf` and pub/priv keys)
- `/etc/dromos/peleon-peer.conf` (the WG peer block for the backbone)
- `/var/cache/dromos/ipset-ru4.cache`
- `users.json` (allocation table)

Output is `tar+age`-encrypted with your passphrase. **Do not store the passphrase next to the backup.**

```bash
scripts/dromos-backup restore root@new-phebes.example.ru ~/dromos-backup/
```

Restores the inverse and reloads services. Used by [rebuild-phebes.md](rebuild-phebes.md).

## Geobase

Updated weekly via the systemd timer `dromos-geo-update.timer`. Manual refresh:

```bash
dromos-geo-update
```

This downloads `ru-aggregated.zone` from ipdeny, validates it (≥100 CIDRs, no obvious garbage), writes a new ipset, swaps atomically, and caches under `/var/cache/dromos/`.

Switching geobase source: edit `/etc/dromos/geo.conf`. The script supports any URL serving one CIDR per line.

## Healthcheck

```bash
dromos-healthcheck            # auto-detects role
dromos-healthcheck --role peleon   # force role if needed
```

The check is **role-aware**. It resolves the host role from (in order) the
`--role` flag, the `DROMOS_ROLE` environment variable, `/etc/dromos/routing.env`
(rendered by the common role), and finally auto-detection (a host with a
`dromos-client` interface is Phebes; otherwise Peleon).

Both roles check:

- backbone handshake age < 180s
- `nft` table `inet dromos` present with the expected chains
- egress interface present (the host's NAT/default-route device)
- `ping -c 1` the backbone peer (Peleon pings Phebes `10.77.0.1`; Phebes pings
  Peleon `10.77.0.2`)

Phebes additionally checks:

- `dromos-client` up and has peers
- RU ipset non-empty and not older than 14 days
- `ip rule` fwmark entries for both marks

Peleon has no `dromos-client`, no RU ipset, and no fwmark policy routing — it
only terminates the backbone and NATs out its egress — so those Phebes-only
probes are skipped there rather than reported as misleading failures.

Exit code 0 on all OK; non-zero with summary on any failure. Suitable for monitoring.

### Whole-path diagnostics from the control machine

`dromos-healthcheck` runs *on a host* and answers "is this host healthy?".
`dromos-diagnose` runs *from your workstation* against the inventory and answers
"is the whole two-hop path healthy, and if not, which hop/layer is broken?". It
reaches both nodes over SSH, runs `dromos-healthcheck` remotely on each, and adds
cross-host checks the host-local probe can't do (the raw Phebes→Peleon UDP path,
both-ends handshake/counter comparison, Peleon's UFW route-allow):

```bash
scripts/dromos-diagnose -i ansible/inventory/prod.ini
```

It is read-only and non-invasive (never edits hosts, installs packages, or
restarts services) and never prints private keys or client configs. Exit code 0
when green (warnings allowed), 1 on any FAIL. See
[docs/troubleshooting.md](troubleshooting.md#start-here-dromos-diagnose-whole-path-check-from-your-workstation)
for how to read the output, and `scripts/dromos-diagnose --help` for flags.

### Runtime-state self-heal (Phebes)

Dromos owns two kinds of state, and the difference matters when diagnosing
intermittent Phebes failures:

- **Persistent state** lives on disk and survives reboots because Ansible writes
  it: the systemd units, the rendered `nft` ruleset file, the WireGuard configs.
- **Runtime state** lives only in the kernel and has to be *re-created* by helper
  commands after boot or after the relevant interface comes up:
  - the `ip rule` fwmark entries (`0x42` → table `100`, `0x43` → table `101`),
  - the default routes inside tables `100` (RU-direct) and `101` (foreign/Peleon),
  - the geobase set `nft … set inet dromos dromos_ru4`.

Those re-creating commands (`dromos-routing apply …`, `dromos-geo-update`) run
from the WireGuard `wg-quick` PostUp hooks — but **only when an interface
actually (re)starts**. On a converged host whose WG units stay up across a
network event, a routing-table teardown, or a partial restart, the policy rules
and routes can silently drift away *while WireGuard itself stays healthy*. The
symptom is a `dromos-diagnose` run that shows a healthy WireGuard backbone but
`[FAIL]` lines for the missing fwmark rules (and sometimes the `nft`/ipset
checks), with a manual `sudo /usr/local/sbin/dromos-routing apply client`
instantly restoring health.

To close that gap, Phebes runs a periodic self-heal:

- **`/usr/local/sbin/dromos-assert-runtime`** — an idempotent script that checks
  each piece of runtime state and repairs only what is missing: starts
  `dromos-nft.service` if the table is gone, reloads the geobase from its cache
  (or runs a geo update if there is no cache), and runs
  `dromos-routing apply client` / `apply backbone` when the fwmark rules or the
  table-101 default route have drifted. It **never** resets the firewall,
  touches the host default route, or restarts WireGuard, so it is safe to run on
  a live host carrying your SSH session.
- **`dromos-routing-assert.timer`** runs it shortly after boot
  (`OnBootSec`, default `30s`) and then on a light interval
  (`OnUnitActiveSec`, default `1min`). Tune via
  `dromos_assert_runtime_boot_sec` / `dromos_assert_runtime_interval`, or disable
  with `dromos_routing_assert_timer_enabled: false`.

Inspect it with:

```bash
systemctl status dromos-routing-assert.timer
journalctl -u dromos-routing-assert.service --since '1 hour ago'
sudo /usr/local/sbin/dromos-assert-runtime   # run it on demand
```

This complements (does not replace) the `site.yml` converge-time assertion: the
playbook reasserts routing on every run, and the timer keeps it asserted between
runs.

## Logs

| What                | Where                                    |
|---------------------|------------------------------------------|
| WG handshakes       | `journalctl -u wg-quick@dromos-client`   |
| Backbone WG         | `journalctl -u wg-quick@dromos-backbone` |
| Shadowsocks         | `journalctl -u shadowsocks-libev-server@dromos` |
| OpenVPN             | `journalctl -u openvpn-server@dromos`    |
| Geo updates         | `journalctl -u dromos-geo-update.service`|
| Healthcheck (cron)  | `journalctl -u dromos-healthcheck.service`|

## Routine maintenance

- **Weekly:** review `dromos-user list`, prune unused users.
- **Monthly:** rotate the operator backup; verify a restore in the Docker lab.
- **Quarterly:** review the geobase source; consider rotating Phebes IP if RKN starts blocking.
