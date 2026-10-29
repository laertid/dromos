# Move or rebuild Phebes without losing users

Use this runbook when Phebes moves to a new VPS or hosting provider, or when the
old VPS is broken and you need a new one. Users keep their current configs. The
only thing they have to do is turn the tunnel off and on after the DNS change.

Expected downtime: about the DNS TTL plus 1–2 minutes.

## What has to move

Every user credential is stored on Phebes. Peleon stores none.

| Path on Phebes | Why it matters |
|---|---|
| `/etc/dromos/secrets/server.key`, `server.pub` | Client-facing WireGuard key. Every client config contains `server.pub`, so a new key would break every client. |
| `/etc/dromos/secrets/backbone.key`, `backbone.pub` | Backbone key. If you keep it, Peleon's config stays valid. |
| `/etc/dromos/secrets/clients/` | Per-user configs and keys, used by `dromos-user show`. |
| `/etc/dromos/users.json` | User names and their VPN IPs. |
| `/var/cache/dromos/` | RU geobase cache, so routing works before the first download. |
| `/etc/dromos/jump/` | Only if the jump hub (`jump/`) runs on Phebes. Its keys are already in `secrets/jump/`. |

You do not need `/etc/dromos/client.env` or `routing.env`. Ansible renders them
from the inventory.

## 1. Back up the old Phebes

Do this now, and again after every `dromos-user add` or `remove`. Run it on your
own PC. In these commands `-t` lets `sudo` ask for your password.

```bash
OLD=laertid@OLD_PHEBES_IP
KEY=~/.ssh/joker
FILES="etc/dromos/secrets etc/dromos/users.json var/cache/dromos"   # add etc/dromos/jump if jump runs here

ssh -t -i $KEY $OLD "sudo sh -c 'umask 077 && tar -C / -czpf /tmp/dromos-backup.tgz $FILES' && sudo chown \$(id -un) /tmp/dromos-backup.tgz"
scp -i $KEY $OLD:/tmp/dromos-backup.tgz ~/dromos-backup-$(date +%F).tgz
ssh -i $KEY $OLD 'rm -f /tmp/dromos-backup.tgz'
chmod 600 ~/dromos-backup-*.tgz
```

Check the archive with `tar -tzf ~/dromos-backup-YYYY-MM-DD.tgz`. It should list
`server.key`, `backbone.key`, `users.json` and one directory per user under
`clients/`.

The archive holds private keys. Store it encrypted, for example
`age -p` or an encrypted disk, and never commit it.

If the old VPS is already gone, use your latest backup.

## 2. Prepare the new VPS

You need Ubuntu 22.04 or 24.04 with a Russian IP, SSH access for a user with
sudo, and these inbound ports open in the provider firewall: UDP 51820 for
clients and TCP 22 for SSH. Phebes connects out to Peleon, so it does not need
an inbound backbone port.

Restore the archive **before** you run Ansible. The keygen tasks only run when
the keys are missing, so a restored host keeps the old keys and users.

```bash
NEW=laertid@NEW_PHEBES_IP
scp -i $KEY ~/dromos-backup-YYYY-MM-DD.tgz $NEW:/tmp/dromos-backup.tgz
ssh -t -i $KEY $NEW 'sudo tar -C / -xzpf /tmp/dromos-backup.tgz && rm -f /tmp/dromos-backup.tgz'
ssh -t -i $KEY $NEW 'sudo ls -l /etc/dromos/secrets /etc/dromos/users.json'
```

Update the inventory with the new Phebes IP. Keep the same Peleon IP and the
same Phebes DNS name:

```bash
scripts/dromos-init-inventory --force
```

## 3. Cut over

Never run two Phebes hosts at the same time. Both would use the same backbone
key. Peleon would then keep switching its endpoint between them, and foreign
traffic would break on both.

1. Lower the TTL on the Phebes DNS A record to 60 s, ideally a TTL period ahead.
2. If the old host is still alive, stop it:
   ```bash
   ssh -t -i $KEY $OLD 'sudo systemctl disable --now wg-quick@dromos-client wg-quick@dromos-backbone'
   # if jump runs there too:
   ssh -t -i $KEY $OLD 'sudo systemctl disable --now wg-quick@dromos-jump'
   ```
3. Deploy. Always use `site.yml`, not `phebes.yml`. `phebes.yml` alone fails
   because it gets Peleon's key from the bootstrap play. Add `-K` if your sudo
   asks for a password.
   ```bash
   ansible-playbook -i ansible/inventory/prod.ini ansible/playbooks/site.yml
   ```
4. Check the new host:
   ```bash
   scripts/dromos-diagnose -i ansible/inventory/prod.ini
   ssh -t -i $KEY $NEW 'sudo dromos-user list'     # same users as before
   ```
5. Point the DNS A record at the new Phebes IP.
6. Ask users to turn the tunnel off and on. WireGuard apps resolve the hostname
   only when the tunnel starts. Then check one RU site and one foreign site.
   `https://ifconfig.co/ip` should show Peleon's IP.
7. If jump ran on the old Phebes, reinstall it on the new host with the same
   endpoint. `install.sh` keeps the restored keys and peers:
   ```bash
   cd dromos/jump/server && sudo ./install.sh --endpoint <same endpoint as before>
   ```
   If the jump endpoint was an IP instead of a DNS name, every jump peer needs a
   new config (`jump-server show`).

## Rollback

On the new host, run `sudo systemctl stop wg-quick@dromos-client wg-quick@dromos-backbone`.
On the old host, run `sudo systemctl start wg-quick@dromos-backbone wg-quick@dromos-client`.
Then point DNS back to the old IP.

## Rebuild from scratch (no backup)

If there is no backup, deploy as a clean install (see README). All users then
need new configs from `dromos-user add`. Peleon picks up the new backbone key
automatically on the same `site.yml` run.
