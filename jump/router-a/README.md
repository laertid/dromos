# Router A: send a whole LAN out via B

An OpenWrt router in city A takes the place of laptop A. Every device behind it (laptops, phones, TV) exits via B without any per-device setup. Target: OpenWrt 24.10/25.12 (fw4, `apk` or `opkg`). For the ASUS RT-AX52, flash OpenWrt first ([rt-ax52-openwrt.md](../rt-ax52-openwrt.md)).

The stock ASUS firmware also works as A, through its WireGuard client in VPN Fusion: import the `.conf` from `jump-server show a-router` and apply it to all devices. This page covers OpenWrt, which adds the kill switch and DNS handling below.

## 1. Create the peer on S

```bash
sudo jump-server add a-router --role client
sudo jump-server show a-router --format openwrt > a-router.env
```

## 2. Copy and run

```bash
scp -O a-router.env jump/router-a/setup.sh root@192.168.1.1:/tmp/
ssh root@192.168.1.1 'sh /tmp/setup.sh /tmp/a-router.env && rm /tmp/a-router.env'
```

Run it from a LAN device. With the kill switch on, LAN keeps internet only while the tunnel is up.

Options (set them before `sh`, for example `JUMP_KILL_SWITCH=0 sh /tmp/setup.sh ...`):

| Variable | Default | Effect |
|---|---|---|
| `JUMP_KILL_SWITCH` | `1` | Disables the stock `lan → wan` forwarding, so LAN never falls back to the local ISP |
| `JUMP_DISABLE_IPV6` | `1` | Turns off RA and DHCPv6 on LAN, because the tunnel is IPv4 only |

## How it routes

| UCI section | Settings |
|---|---|
| `network.jump` | `proto wireguard`, `10.88.0.N/32`, MTU 1380 |
| `network.jump_server` | Peer S: endpoint, keepalive 25, `allowed_ips 0.0.0.0/0`, `route_allowed_ips 0` |
| `network.jump_rule` | `ip rule` priority 1880: `in lan lookup 188` |
| `network.jump_default` | `0.0.0.0/0 dev jump table 188` |
| `network.jump_dns1..N` | `/32` routes for the DNS servers via `jump` |
| `dhcp.@dnsmasq[0]` | `noresolv 1` and `server` = the jump DNS servers, so LAN DNS exits via B |
| `firewall.jump_zone` | Zone `jump`: masquerade, MSS clamping, input/forward REJECT |
| `firewall.jump_fwd_lan` | Forwarding `lan → jump` |

Only traffic that enters from LAN is policy-routed into the tunnel. The router's own traffic, including the WireGuard handshake to S, uses WAN as usual, so there is no routing loop and no need for an endpoint exception route. LAN is NATed into the tunnel, so S only knows this router as one `/32` client.

## 3. Check

From any device behind the router:

```bash
curl -4 ifconfig.me    # B's public IP
ping 10.88.0.2         # B
```

On the router, run `wg show jump` (expect a recent handshake) and `ip rule show pref 1880`.

## Notes

- **Tunnel down.** LAN loses internet when the kill switch is on, which is the point. dnsmasq on the router may then resolve over WAN, but forwarded traffic is still blocked.
- **Exceptions.** To let a device bypass the tunnel, add a higher-priority rule, for example `uci set network.bypass=rule; uci set network.bypass.src='192.168.1.50/32'; uci set network.bypass.lookup='main'; uci set network.bypass.priority='1870'`. Re-enable `lan → wan` forwarding for it too (`JUMP_KILL_SWITCH=0`).
- **Guest or other zones.** Only the `lan` interface is routed. Other networks keep using WAN.

## Remove

```bash
ssh root@192.168.1.1 'sh /tmp/setup.sh --remove'
```

This restores `lan → wan`, the original dnsmasq resolver settings and LAN IPv6. The originals are recorded in `/etc/dromos-jump.state` on the first run.
