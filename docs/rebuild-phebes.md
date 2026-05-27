# Rebuilding Phebes

This runbook recreates Phebes on a new VPS. Expected user-visible downtime: **~10 minutes** (mostly DNS propagation; if you use a short TTL it's closer to 2 min).

## Prerequisites

- A fresh Ubuntu 22.04 or 24.04 VPS with a Russian IP. Root SSH access.
- The operator's backup directory (`~/dromos-backup/`) containing the previous Phebes secrets and user list.
- Peleon is up and reachable. You have not changed Peleon.
- DNS for your user-facing hostname (`vpn.example.ru`) is under your control with TTL ≤ 300s.

## Step 0 — pre-flight on your workstation

```bash
ls ~/dromos-backup/
# expect: server.key server.pub users.json peleon-peer.conf nftables-cache.nft ipset-ru4.cache
```

If any of these are missing, stop and recover them before continuing.

## Step 1 — provision the new VPS

Get the new IP. Add it to inventory:

```bash
cd dromos
cp ansible/inventory/sample.ini ansible/inventory/prod.ini
$EDITOR ansible/inventory/prod.ini
#   [phebes]
#   phebes ansible_host=NEW.IP.ADDR ansible_user=root
#   [peleon]
#   peleon ansible_host=PELEON.IP   ansible_user=root
```

Sanity-check connectivity:

```bash
ansible -i ansible/inventory/prod.ini phebes -m ping
```

## Step 2 — install Dromos

```bash
ansible-playbook -i ansible/inventory/prod.ini ansible/playbooks/phebes.yml
```

This installs WG, nftables, the Dromos systemd units, the geo-update timer, and the SS/OVPN servers. It does **not** start client traffic yet because there are no peers.

## Step 3 — restore secrets and users

```bash
scripts/dromos-restore ~/dromos-backup/
```

This:

1. SCPs `server.key`, `server.pub` into `/etc/dromos/secrets/` on Phebes.
2. Re-creates each user from `users.json` (their existing client public keys are added as WG peers; their existing private keys do NOT leave your laptop, the server only needs the publics).
3. Adds the existing Peleon peer block from `peleon-peer.conf` so the backbone WG comes up.
4. Restores the cached `ipset-ru4.cache` so we don't wait for the first geo-update.
5. Reloads `dromos-client`, `dromos-backbone`, nftables, ip rules.

## Step 4 — verify on the server

```bash
ssh root@NEW.IP.ADDR
dromos-healthcheck      # installed by the playbook
```

Expected output (all `OK`):

```
[OK]  dromos-client interface up, peers: 3
[OK]  dromos-backbone handshake age: 12s
[OK]  ipset dromos_ru4 entries: 312
[OK]  ip rule fwmark 0x42 → 100 present
[OK]  ip rule fwmark 0x43 → 101 present
[OK]  nftables table inet dromos present, 5 chains
[OK]  ping 10.77.0.2 via dromos-backbone: 14ms
```

If anything is `FAIL`, see [troubleshooting.md](troubleshooting.md). **Do not change DNS yet.**

## Step 5 — smoke test through the new Phebes

On your laptop, temporarily edit one client config to point `Endpoint` at the new IP. Connect.

```bash
curl -s https://ifconfig.co/json | jq .country
# foreign destinations should report "Italy" (exits via Peleon)
curl -s --resolve yandex.ru:443:87.250.250.242 https://yandex.ru -o /dev/null -w '%{remote_ip}\n'
# RU destinations should remote_ip = 87.250.250.242 with no Peleon in path
```

Run `mtr` to one RU host and one non-RU host; the first hop after your tunnel is Phebes in both cases, but the non-RU mtr should show Peleon's IT IP further down.

## Step 6 — flip DNS

```bash
# in your DNS provider:
#   vpn.example.ru   A   NEW.IP.ADDR   TTL 60
```

Users with old Endpoint cached will fail until their client retries (WG retries every ~15s).

## Step 7 — decommission the old VPS

Wait 30 minutes for any stragglers. Then:

```bash
ssh root@OLD.IP.ADDR systemctl stop dromos-client dromos-backbone
# verify no live conns
ssh root@OLD.IP.ADDR wg show dromos-client | grep -c handshake
```

When count is 0 for a few minutes, terminate the old VPS in your provider's panel.

## Downtime budget

| Step                    | Duration             |
|-------------------------|----------------------|
| Provision new VPS       | 2–5 min (provider)   |
| Playbook                | 2–3 min              |
| Restore + verify        | 1 min                |
| DNS propagation @ TTL 60| 1–2 min              |
| **User-visible total**  | **~10 min**          |

Users mid-stream will see their tunnel drop and reconnect once the DNS flips. WireGuard reconnects in seconds; SS/OVPN may take longer.

## Rollback

If verification fails: do not flip DNS. The old Phebes is still serving. Tear down the new one and try again with the failure captured.

If you already flipped DNS and the new Phebes is broken: flip DNS back. WG clients will pick up the rollback within a TTL.
