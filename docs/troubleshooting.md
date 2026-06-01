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

### `wg: command not found` in bootstrap keygen

Symptom (during `ansible-playbook ... site.yml`, on a fresh VPS):

```
TASK [Ensure backbone keypair exists]
fatal: [phebes]: FAILED! => "/bin/bash: wg: command not found"
```

Cause: `bootstrap.yml` runs first (before the `common` role) and generates the
backbone keypair with `wg genkey`, but on a clean install `wireguard-tools`
isn't installed yet — that install lives in the `common` role, which runs in the
later `phebes.yml` / `peleon.yml` plays. So `wg` doesn't exist at keygen time.

Fixed in this repo: `bootstrap.yml` now installs `wireguard-tools` itself
(idempotent `apt`) before the keygen task. The `common` role still installs the
full package set later; that install is a no-op once bootstrap has run. If you
maintain a custom bootstrap, install `wireguard-tools` before any `wg` call.

### `'dromos_secrets_dir' is undefined` in bootstrap.yml

Symptom (during `ansible-playbook ... site.yml`, on a clean install):

```
TASK [Ensure secrets directory exists]
fatal: [...]: FAILED! => "'dromos_secrets_dir' is undefined"
```

Cause: `bootstrap.yml` runs with `roles:` unset, so it never loads the `common`
role's `defaults/main.yml` where the directory paths used to live. The role
plays (`phebes.yml` / `peleon.yml`) saw the variable, but the role-less
bootstrap play did not.

Fixed in this repo: the shared paths (`dromos_config_dir`, `dromos_secrets_dir`,
`dromos_cache_dir`) now live in `ansible/inventory/group_vars/all.yml`, which
Ansible auto-loads for *every* play in the run — bootstrap included. If you keep
a custom inventory elsewhere, make sure a sibling `group_vars/all.yml` defines
these, or pass them with `-e`.

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

## Backbone never handshakes (UFW silently drops the WG port)

Symptom: `wg show dromos-backbone` shows no handshake, yet `tcpdump -ni any udp
port 51821` on the *receiving* host shows the peer's packets arriving. The
packets reach the NIC but never reach WireGuard.

Cause: a host firewall (commonly UFW with `default deny incoming` and only
`22/tcp` allowed) drops the inbound WireGuard UDP. This was seen live on Peleon:
Phebes' UDP reached `Peleon:51821` but UFW dropped it before WireGuard.

```bash
ufw status verbose            # is it active? what's allowed?
```

Fix: the Dromos roles now open the required UDP ports **when UFW is active**,
idempotently and additively (never `ufw reset` / `ufw disable` / policy change),
re-running is a no-op:

- Peleon: `51821/udp` (backbone)
- Phebes: `51820/udp` (client) + `51821/udp` (backbone)
- SSH (`dromos_ssh_port`, default `22/tcp`) is re-asserted on both so opening
  these rules can never sever your session.

Re-run the relevant play (`site.yml`, or `peleon.yml`/`phebes.yml`) and the
allow rules appear. To verify manually:

```bash
ufw status | grep -E '5182[01]/udp'
```

If you manage the firewall some other way (raw nftables/iptables, cloud
security groups), open the same ports there — Dromos only manages UFW when UFW
is the active firewall.

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

4. **UFW is dropping forwarded traffic on Peleon.** If UFW is active with a
   default-deny *routed* policy, allowing the backbone UDP port lets the tunnel
   handshake but UFW still drops decapsulated client traffic on the FORWARD
   path. Symptom: Phebes sends MB to Peleon but receives little back. Check:

   ```bash
   ufw status verbose | grep -i 'Default:'        # → '... deny (routed)'
   iptables -S ufw-user-forward                   # should ACCEPT in dromos-backbone out <egress>
   ```

   The playbook adds this automatically (`ufw route allow in on dromos-backbone
   out on <egress>`). To apply by hand on a live host:

   ```bash
   ufw route allow in on dromos-backbone out on enx5 comment 'dromos: backbone to internet'
   ```

   (replace `enx5` with Peleon's actual egress interface).

## "DNS doesn't resolve but I can reach IPs directly"

Phebes runs **no** DNS resolver. If clients were handed `DNS = 10.66.0.1`
(the tunnel gateway), every lookup fails while direct IPs still work. Fix by
setting `dromos_client_dns` to public resolvers (default `1.1.1.1, 1.0.0.1`)
and re-running the Phebes playbook. **Existing users must regenerate or
hand-edit their configs** afterward (`dromos-user show <name>` prints the
updated config); a client holding an old config keeps the old DNS line.

## "ip rule" entries vanished

A reboot without the systemd unit, or someone ran `ip rule flush`.

```bash
systemctl restart dromos-routing.service
# this re-applies all ip rule / ip route commands from /etc/dromos/routing.conf
```

## "I locked myself out of SSH after running site.yml" (port 22 open, banner hangs)

**Symptom:** from outside, TCP/22 is *open* (the SYN-ACK comes back) but the SSH
banner never arrives — the connection hangs right after the TCP handshake and
times out. Ping may still work. This is the classic asymmetric-routing
lock-out: the host's SYN-ACK leaves via the input/local path, but the
*established* reply packets (banner + everything after) get policy-routed out a
Dromos interface and never return to you.

**Root cause (fixed in this repo):** the Phebes backbone WireGuard config used
`AllowedIPs = ..., 0.0.0.0/0` with no `Table` setting. `wg-quick` interprets a
default route in `AllowedIPs` as "route everything through me": it installs a
`default dev dromos-backbone` route in its own table plus a
`suppress_prefixlength 0` ip rule, which steals **all** host traffic that isn't
marked with wg's own fwmark — including sshd's replies. The fix is
`Table = off` on every Dromos `wg-quick` interface (Dromos does its own
policy routing via `dromos-routing`), plus an explicit SSH bypass in the
`mark_local` nft chain and a deploy-time deadman that auto-reverts.

### Recover from the provider console / serial console

You do **not** need to rebuild. Run these on the box (they only undo Dromos):

```bash
# 1. Stop the interfaces that installed the hijacking routes/rules.
systemctl stop wg-quick@dromos-backbone wg-quick@dromos-client

# 2. Tear down Dromos policy routing (idempotent, safe).
/usr/local/sbin/dromos-routing teardown backbone
/usr/local/sbin/dromos-routing teardown client

# 3. Remove any leftover wg-quick hijack rules/route, if present.
#    wg-quick's default-route takeover uses fwmark 0xca6c / table 51820.
ip rule del not fwmark 0xca6c table 51820 2>/dev/null || true
ip rule del table main suppress_prefixlength 0 2>/dev/null || true
ip route flush table 51820 2>/dev/null || true

# 4. Confirm the host default route + main lookup are intact.
ip rule show          # 'from all lookup main' should be priority 32766
ip route show default # should point at the provider gateway via eth0
```

SSH should recover immediately. Then pull the fixed configs and redeploy:

```bash
# the rendered config still has the bad line until you redeploy
grep -n '0.0.0.0/0' /etc/wireguard/dromos-backbone.conf
grep -n '^Table'    /etc/wireguard/dromos-backbone.conf   # should be 'Table = off'
```

If a future Dromos change ever does this again, the deploy-time deadman
(`dromos_deadman_enabled`, default on) tears Dromos down ~5 min after the unit
restart unless the deploy confirms SSH survived — so an unattended run can no
longer leave you locked out.

### Other (non-Dromos) lockouts

Dromos never touches the `inet filter` / `ip filter` tables or SSH config, so a
*drop*-based lockout is not Dromos:

1. Use your provider's web console / serial console.
2. `nft list ruleset` and check no chain has a `drop` rule on TCP/22.
3. `ip rule show` — confirm `from all lookup main` still has priority < 32766.

If you truly can't recover via console: snapshot the disk, rebuild Phebes from
scratch, restore from backup.

## Egress / NAT goes out the wrong interface (not eth0)

Symptom: clients can reach the backbone but RU-direct exit (Phebes) or all
exit traffic (Peleon) fails, and the rendered nft rules reference `eth0` while
the host's real uplink is something else (live Peleon's default route was
`enx5`).

Dromos no longer hardcodes `eth0`. `dromos_egress_ifname` now defaults per host
to `ansible_default_ipv4.interface` (the auto-detected default-route device),
and the generated inventory leaves it **unset** so each host detects its own.

Check what was detected/rendered:

```bash
ip route show default                     # the real egress device
cat /etc/dromos/routing.env               # DROMOS_EGRESS_IF used at runtime
grep -E 'oifname' /etc/dromos/dromos.nft  # device in the NAT/forward rules
```

If auto-detection picks the wrong interface (multi-homed host, unusual routing),
pin it explicitly in inventory — per host or globally:

```ini
[peleon]
peleon-1 ansible_host=... ansible_user=root dromos_egress_ifname=enx5
```

Then redeploy. `dromos-routing` also auto-detects the default-route interface at
runtime as a fallback, so it stays correct even if the env file is absent.

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
