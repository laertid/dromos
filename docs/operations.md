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
- Writes the user-facing config to `/etc/dromos/secrets/clients/alice/alice.conf`.
- Prints the config to stdout (also QR code if `qrencode` is installed) so you can hand it off.

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
dromos-healthcheck
```

Runs all of:

- both WG interfaces up and have peers
- backbone handshake age < 180s
- ipset non-empty and not older than 14 days
- `ip rule` entries for both marks
- `nft` table `inet dromos` present with the expected chains
- `ping -c 1` Peleon over the backbone

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
