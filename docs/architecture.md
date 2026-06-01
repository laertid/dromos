# Dromos Architecture

## Goals

1. Two-hop VPN: client → Phebes (RU entry) → Peleon (IT exit), for non-RU destinations.
2. Single-hop pass-through for RU destinations: client → Phebes → RU dst.
3. Single client connection. The user does not pick a hop.
4. Multiple client-facing protocols so users behind hostile networks still get in.
5. No collateral damage to either host (SSH, existing tunnels, Docker).

## Topology

```
                       ┌────────────────────────────────────────┐
                       │                  Phebes (RU)           │
client ── WG/SS/OVPN ──┤  dromos-client (10.66.0.1/24)          │
                       │                                        │
                       │  routing decision:                     │
                       │    dst ∈ RU set  → fwmark 0x42 → table 100 (default via eth0) │
                       │    else          → fwmark 0x43 → table 101 (default via dromos-backbone) │
                       │                                        │
                       │  dromos-backbone (10.77.0.1/30) ──────┐│
                       └────────────────────────────────────────┘│
                                                                 │ WG, UDP/51821
                                                                 ▼
                                                       ┌──────────────────┐
                                                       │   Peleon (IT)    │
                                                       │ dromos-backbone  │
                                                       │ (10.77.0.2/30)   │
                                                       │ MASQUERADE → eth0│
                                                       └──────────────────┘
```

## Interfaces, tables, marks

| Resource              | Name / value          | Owner    | Purpose                                       |
|-----------------------|-----------------------|----------|-----------------------------------------------|
| WG interface (clients)| `dromos-client`       | Phebes   | Client tunnels terminate here                 |
| WG interface (back.)  | `dromos-backbone`     | Phebes, Peleon | Phebes↔Peleon transport                 |
| Client subnet         | `10.66.0.0/24`        | Phebes   | Client IPs (`.1` = Phebes, `.2..` = users)    |
| Backbone subnet       | `10.77.0.0/30`        | both     | `.1` = Phebes, `.2` = Peleon                  |
| Routing table 100     | `dromos_ru`           | Phebes   | Default via host eth0 (direct exit)           |
| Routing table 101     | `dromos_foreign`      | Phebes   | Default via `dromos-backbone` (→ Peleon)      |
| fwmark `0x42`         | `DROMOS_MARK_RU`      | Phebes   | Selects table 100                             |
| fwmark `0x43`         | `DROMOS_MARK_FOREIGN` | Phebes   | Selects table 101                             |
| nftables table        | `inet dromos`         | both     | All Dromos rules. No other table is touched.  |
| nftables ipset        | `dromos_ru4`          | Phebes   | RU IPv4 CIDRs from ipdeny                     |

These names are chosen so a junior operator can `ip -d link show dromos-client`, `ip rule | grep dromos`, `nft list table inet dromos` and see the entire Dromos footprint without grepping the rest of the system.

## Packet walk

A client at `10.66.0.5` sends a packet to `8.8.8.8`:

1. Client's WG app encrypts → UDP/51820 to Phebes public IP.
2. Phebes kernel decrypts, packet appears on `dromos-client` with src `10.66.0.5`, dst `8.8.8.8`.
3. nftables `inet dromos`, chain `prerouting` (type filter, hook prerouting):
   - lookup `dromos_ru4` for `8.8.8.8` → **miss** → `meta mark set 0x43`.
   - If hit, `meta mark set 0x42` instead.
4. `ip rule` matches `fwmark 0x43 lookup 101` → table `dromos_foreign` → default via `dromos-backbone`.
5. nftables `postrouting` masquerades to `10.77.0.1` and the packet leaves over the backbone WG.
6. Peleon decrypts, packet appears on its `dromos-backbone` with src `10.77.0.1`, dst `8.8.8.8`.
7. Peleon `postrouting` MASQUERADEs to its eth0 IP and forwards normally.
8. Return path mirrors via conntrack.

For `dst = 87.250.250.242` (yandex.ru, in `dromos_ru4`):

3'. mark `0x42`, table 100, default via host eth0 → direct exit from Phebes. Peleon never sees it.

## Why fwmark + policy routing (not VRF, not network namespaces)

- VRF would require moving the host default route into a VRF, which violates "don't touch the host default route."
- Network namespaces would isolate cleanly but force us to manage two separate routing universes and re-publish ports — overkill for 1–10 users.
- fwmark + ip rule lookup is the lightest mechanism that lets us pick a different next-hop per-flow without disturbing anything else.

## Geobase

- Source: `https://www.ipdeny.com/ipblocks/data/aggregated/ru-aggregated.zone` (RU IPv4 aggregated CIDRs, ~300 entries).
- Refresh: weekly via `scripts/dromos-geo-update`. Atomic swap of the ipset.
- Fallback: if download fails, keep current set. Never empty.
- IPv6: out of scope for v1. Disabled on Dromos interfaces.

## Client protocols

### WireGuard (primary)

- UDP/51820 on Phebes public IP.
- One peer per user. Public key in server config, private key only on client and in operator backup.
- AllowedIPs on client: `0.0.0.0/0` (full tunnel). DNS handed to clients: public resolvers (`1.1.1.1, 1.0.0.1` by default, via `dromos_client_dns`). Phebes runs no resolver, so DNS queries ride the full tunnel and exit through Dromos/Peleon like any other foreign traffic.
- MTU: 1380 (safe for double WG encap).

### Shadowsocks (secondary)

- TCP/8388 on Phebes public IP. AEAD cipher `chacha20-ietf-poly1305`.
- Documented for two modes:
  - **Proxy mode** (most apps): app-level SOCKS/HTTP redirector. Limited to the app's traffic.
  - **TUN mode** (e.g. Outline client, ShadowsocksR-Plus on Android, sing-box on iOS): full tunnel.
- SS server runs on Phebes with the same fwmark-on-output trick (`meta mark set 0x43` for non-RU dst), so the second-hop logic still applies.

### OpenVPN (fallback)

- TCP/443. Looks like HTTPS. Slow, last resort.
- `tls-crypt`, `cipher AES-256-GCM`, `auth SHA256`.
- Pushes the same routes and DNS as WireGuard. Same fwmark logic applies on the `tun-dromos` interface.

## Failure modes & behavior

| Failure                       | Behavior                                                                     |
|-------------------------------|------------------------------------------------------------------------------|
| Peleon unreachable            | Foreign traffic blackholes. RU traffic still works. Healthcheck pages.        |
| Geobase download fails        | Old ipset retained. Logged. Healthcheck warns after 14 days stale.            |
| Phebes reboots                | systemd units restore WG, nftables, ip rules. ipset reloaded from disk cache. |
| User key compromised          | `scripts/dromos-user remove <name>` revokes within seconds.                   |
| Backbone WG handshake stale   | systemd timer pings `10.77.0.2` every 30s, bounces interface if 3x fail.      |

## Decisions log (ADR-lite)

- **ADR-001:** WG primary, SS secondary, OVPN fallback. Rationale: WG is fastest and best-supported; SS handles UDP-blocked nets; OVPN/TCP/443 is the universal last resort.
- **ADR-002:** IPv4-only v1. Rationale: avoid v6 leaks; geobase v6 story is weaker; small user base unaffected.
- **ADR-003:** fwmark + policy routing on Phebes, not netns/VRF. Rationale: minimal host disruption.
- **ADR-004:** ipdeny aggregated zone. Rationale: small (~300 CIDRs), well-maintained, no API key needed. Easy to swap for RIPE or local list later.
- **ADR-005:** Dedicated interface/table/chain names with `dromos-` / `dromos_` prefix. Rationale: human-greppable footprint; coexists with pre-existing wg1 and Docker rules.
- **ADR-006:** No secret material in git. Generated under `/etc/dromos/secrets/` (mode 0700, root). Operator backs up to their PC.
