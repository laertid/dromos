# Security

## Threat model

In scope:

- Passive eavesdropper on the path client→Phebes or Phebes→Peleon.
- Active man-in-the-middle attempting to inject or modify traffic.
- A network operator blocking VPN protocols (UDP, then non-443 TCP, then TLS-like only).
- Compromise of a single user's device (limit blast radius).
- Loss of a VPS provider account or a single VPS being seized.

Out of scope:

- A nation-state with physical access to both VPSs or to Anthropic-grade traffic analysis at both endpoints. Dromos is not Tor.
- Endpoint compromise of the operator's workstation (you must protect your backup).
- Application-layer fingerprinting on the egress side.

## Cryptography

| Component  | Algorithm                                |
|------------|------------------------------------------|
| WireGuard  | Curve25519 + ChaCha20Poly1305 + BLAKE2s. As shipped. |
| Shadowsocks| `chacha20-ietf-poly1305` AEAD            |
| OpenVPN    | `cipher AES-256-GCM`, `auth SHA256`, `tls-crypt` (PSK wraps the control channel; defeats simple DPI) |
| Backbone WG| Same as WG above                          |

Keys are generated on the server with `wg genkey` / `openvpn --genkey secret`. Entropy assumed adequate on any modern Linux VPS.

## Key storage

```
/etc/dromos/secrets/                       # mode 0700, root:root
├── server.key, server.pub                 # Phebes client-facing WG
├── backbone.key, backbone.pub             # Phebes side of backbone WG
├── peleon-pub.key                         # Peleon's public key (no priv on Phebes)
├── clients/<name>/{priv,pub,preshared}    # per-user
├── shadowsocks.psk
└── openvpn/{ca.key,ca.crt,server.{key,crt},dh.pem,ta.key}
```

The server retains client `priv` files only to print/QR them later. Operator backup is the canonical copy. After a clean handoff to the user you can delete the on-server priv (cron rotation: `dromos-user prune-keys` deletes any priv older than 30 days that has been backed up).

## What is NOT in git

- Anything under `/etc/dromos/secrets/`.
- Inventory files with real hostnames/IPs (use `inventory/sample.ini`).
- `~/dromos-backup/` (your operator backup).
- `*.conf` files that contain a real private key.

`.gitignore` enforces this for the obvious patterns. CI also rejects any blob matching common key headers (see `.github/workflows/secret-scan.yml`).

## SSH hygiene

- Use key-only auth. The playbook does not change `sshd_config` by default — if your provider image starts with password auth, harden it yourself with `usermod`/`PasswordAuthentication no` **before** running Dromos. Dromos never touches the SSH daemon.
- Add `Match Address` rules in your own SSH config to limit who can reach Phebes/Peleon admin SSH if your firewall is upstream.

## Network exposure

| Host    | Port      | Proto | Audience                |
|---------|-----------|-------|-------------------------|
| Phebes  | 22        | TCP   | operator only (you)     |
| Phebes  | 51820     | UDP   | clients (WG)            |
| Phebes  | 51821     | UDP   | backbone (to Peleon)    |
| Phebes  | 8388      | TCP   | clients (SS)            |
| Phebes  | 443       | TCP   | clients (OVPN)          |
| Peleon  | 22        | TCP   | operator only (you)     |
| Peleon  | 51821     | UDP   | only Phebes (firewall)  |

Peleon's `51821/udp` is rate-limited and only accepts packets from Phebes's public IP. This is enforced by a Dromos nftables rule, not by trusting the WG handshake alone.

When UFW is the active host firewall, the Dromos roles additively `ufw allow`
the WireGuard UDP ports above (Peleon `51821`; Phebes `51820`+`51821`) and
re-assert the SSH port — they never reset UFW or change its default policy.
Without this, a `default deny incoming` UFW silently drops the inbound WG UDP
and the backbone never handshakes (see
[docs/troubleshooting.md](troubleshooting.md#backbone-never-handshakes-ufw-silently-drops-the-wg-port)).

## Logging

Dromos services log via journald. We do **not** log:

- user dst IPs or domains
- packet contents
- per-flow byte counters above what `wg show` provides ephemerally

We do log:

- service start/stop/reload
- WG peer add/remove (name + pubkey)
- geobase update successes/failures

Retention: journald default (7 days). Tune `MaxRetentionSec` in `/etc/systemd/journald.conf` if you want shorter.

## Key compromise response

Single user key leaked:

```bash
dromos-user remove alice
dromos-user add alice   # gives them new keys, new IP
```

Phebes server key suspected leaked: regenerate (`dromos-rekey phebes`), redistribute all client configs (their server pub changes).

Peleon backbone key leaked: regenerate (`dromos-rekey peleon` and `dromos-rekey backbone-on-phebes`). No client churn — the backbone is server-to-server.

## Backup security

The operator backup (`~/dromos-backup/`) holds every private key. Treat it like the master keychain it is:

- Store the encrypted tarball on the operator workstation only.
- Encrypted with `age` (`age-keygen`). Recipient is the operator's age public key.
- Operator's age private key should be on a hardware token or, at minimum, in an OS keyring with a strong passphrase.
- Test restore quarterly using the Docker lab.

## Disclosure

Found a security issue? Open a private security advisory on the GitHub repo. Do not file a public issue.
