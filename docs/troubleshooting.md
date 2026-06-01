# Troubleshooting

Recipes for common Dromos failures. Each starts with the symptom.

## Install / first run

### `set: Illegal option -o pipefail`

Symptom (during `ansible-playbook`):

```
/bin/sh: 1: set: Illegal option -o pipefail
```

Cause: a `shell:` task used bash syntax (`set -o pipefail`) but ran under
`/bin/sh` (dash on Debian/Ubuntu). Fixed in this repo — every backbone/keygen
`shell:` task now sets `args: executable: /bin/bash`. If you hit this in a
custom task, add the same:

```yaml
- ansible.builtin.shell: |
    set -euo pipefail
    ...
  args:
    executable: /bin/bash
```

### `AnsibleUndefinedVariable: 'dromos_peleon_public_key' is undefined`

(or the symmetric `dromos_phebes_backbone_public_key is undefined`)

Cause: the backbone config render ran before the peer node's public key was
known. On a clean install this is now handled automatically by `bootstrap.yml`,
which runs first inside `site.yml` and exchanges both public keys. The fix is
simply to run the full site playbook:

```bash
ansible-playbook -i ansible/inventory/prod.ini ansible/playbooks/site.yml
```

If you are running a **single** node playbook (`phebes.yml` or `peleon.yml`) on
its own, the cross-host exchange doesn't happen — pass the peer's backbone
public key explicitly. The key lives at `/etc/dromos/secrets/backbone.pub` on
each node:

```bash
# rendering Phebes alone -> needs Peleon's backbone public key
ansible-playbook -i ansible/inventory/prod.ini ansible/playbooks/phebes.yml \
  -e dromos_peleon_public_key="$(ssh root@PELEON cat /etc/dromos/secrets/backbone.pub)"

# rendering Peleon alone -> needs Phebes' backbone public key
ansible-playbook -i ansible/inventory/prod.ini ansible/playbooks/peleon.yml \
  -e dromos_phebes_backbone_public_key="$(ssh root@PHEBES cat /etc/dromos/secrets/backbone.pub)"
```

If a node hasn't generated its backbone key yet, generate it first (idempotent):

```bash
ssh root@PELEON 'umask 077; mkdir -p /etc/dromos/secrets; \
  test -f /etc/dromos/secrets/backbone.key || \
  wg genkey | tee /etc/dromos/secrets/backbone.key | wg pubkey > /etc/dromos/secrets/backbone.pub; \
  cat /etc/dromos/secrets/backbone.pub'
```

## "I can connect but nothing loads"

Most likely Phebes is up but the second hop is broken.

```bash
# on Phebes
wg show dromos-backbone
#   look at "latest handshake" — should be < 3 min
ping -c 3 -I dromos-backbone 10.77.0.2
```

If handshake is stale:

```bash
systemctl restart wg-quick@dromos-backbone
```

If still nothing, check Peleon is reachable on the public side:

```bash
# from Phebes
nc -vz -u <peleon_public_ip> 51821
```

UDP `nc` is best-effort; absence of error doesn't prove much, but a clear `No route to host` does.

## "RU sites are slow / show foreign IP"

The ipset is missing or wrong-marked.

```bash
nft list set inet dromos dromos_ru4 | head -5
ipcalc 87.250.250.242   # known yandex IP
nft list set inet dromos dromos_ru4 | grep -c '^'
# should be > 100
```

Force refresh:

```bash
dromos-geo-update
```

Verify a packet is marked correctly:

```bash
nft monitor trace &
ip netns exec dromos-trace ping -c 1 87.250.250.242
# look for "meta mark set 0x42"
```

## "Foreign sites don't load at all"

Three possibilities:

1. **Peleon is down.** `dromos-healthcheck` will say so. Restart Peleon, then `systemctl restart wg-quick@dromos-backbone` on Phebes.

2. **Forwarding is off.** Should never happen if the playbook ran, but:

   ```bash
   sysctl net.ipv4.ip_forward          # → 1
   sysctl net.ipv4.conf.all.forwarding # → 1
   ```

3. **nftables is dropping foreign-marked traffic.** Look at the counters:

   ```bash
   nft list table inet dromos | grep -A2 'chain forward'
   ```

   The `accept` rules should be incrementing. If a `drop` rule is incrementing, that's the bug.

## "ip rule" entries vanished

A reboot without the systemd unit, or someone ran `ip rule flush`.

```bash
systemctl restart dromos-routing.service
# this re-applies all ip rule / ip route commands from /etc/dromos/routing.conf
```

## "I locked myself out of SSH"

You should not be able to with Dromos alone — it never touches the `inet filter` or `ip filter` tables used by the default firewall, nor SSH config. If you did:

1. Use your provider's web console / serial console.
2. `nft list ruleset` and check that no Dromos chain has a `drop` rule on TCP/22.
3. `ip rule show` — confirm `from all lookup main` still has priority < 32766. If Dromos rules are at lower priority somehow, delete them: `ip rule del fwmark 0x42 lookup 100`.

If you can't recover via console: snapshot the disk, rebuild Phebes from scratch, restore from backup.

## Backbone interface flapping

```bash
journalctl -u wg-quick@dromos-backbone --since '1h ago' | grep -i error
```

Most common cause: MTU mismatch with the underlying provider's network. Try `MTU = 1380` (already the default) or step down to `1280`. Edit `/etc/wireguard/dromos-backbone.conf` and restart.

## ipset is empty after a reboot

The cache file is missing or unreadable.

```bash
ls -l /var/cache/dromos/ipset-ru4.cache
dromos-geo-update --force
```

If `ipdeny` is unreachable, point `/etc/dromos/geo.conf` `GEO_URL` at a mirror or a local file.

## Shadowsocks works but only for one app

Expected if the client is in proxy mode (most desktop SS clients are). To get full-tunnel SS, use a TUN-capable client (Outline, sing-box, ShadowsocksR-Plus). See [docs/architecture.md](architecture.md#shadowsocks-secondary).

## OpenVPN/443 connects but is unusably slow

Expected. TCP-over-TCP is slow by design. OVPN/443 is a fallback for when WG and SS are both blocked. Push users to WireGuard whenever possible.

## Diagnostic dump

For a support request, attach:

```bash
dromos-healthcheck --verbose > /tmp/dromos-diag.txt
wg show all >> /tmp/dromos-diag.txt
ip -d link show >> /tmp/dromos-diag.txt
ip rule show >> /tmp/dromos-diag.txt
ip route show table 100 >> /tmp/dromos-diag.txt
ip route show table 101 >> /tmp/dromos-diag.txt
nft list table inet dromos >> /tmp/dromos-diag.txt
journalctl -u 'wg-quick@dromos-*' --since '2h ago' >> /tmp/dromos-diag.txt
```

Redact public keys before sharing.
