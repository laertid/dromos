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
