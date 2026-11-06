# Dromos jump: exit to the internet from another city

`jump` lets machine **A** send all of its internet traffic out through machine **B**, so that websites see B's public IP. Neither A nor B needs a public IP. A server **S** with a public IP is the meeting point, and both A and B dial out to it.

```
 city A                         VPS                         city B
┌────────────┐  WireGuard   ┌──────────────┐  WireGuard   ┌────────────┐   NAT
│ A          │─────────────▶│ S            │─────────────▶│ B          │──────▶ internet
│ laptop or  │  UDP/51830   │ dromos-jump  │  (B dials    │ laptop or  │   (B's public IP)
│ router     │  10.88.0.10+ │ 10.88.0.1    │   out to S)  │ router     │
└────────────┘              └──────────────┘              │ 10.88.0.2  │
                                                          └────────────┘
```

All five parts are interchangeable. A can be a laptop or a whole router LAN, and B can be a Linux box or an OpenWrt router:

| Part | Folder | What it does |
|---|---|---|
| Server S | [`server/`](server/README.md) | WireGuard hub, peer management (`jump-server`), A→B hairpin routing |
| Laptop A | [`laptop-a/`](laptop-a/README.md) | One device sends all IPv4 traffic to B |
| Router A | [`router-a/`](router-a/README.md) | OpenWrt router sends its whole LAN to B, with a kill switch |
| Laptop B | [`laptop-b/`](laptop-b/README.md) | Linux machine is the exit and NATs A's traffic |
| Router B | [`router-b/`](router-b/README.md) | OpenWrt router is the exit (needs no always-on PC) |

For an ASUS RT-AX52 and the "Firmware upgrade unsuccessful" error, see [`rt-ax52-openwrt.md`](rt-ax52-openwrt.md).

## Addressing

| Item | Value | Notes |
|---|---|---|
| Interface on S, laptops | `dromos-jump` | On OpenWrt routers the logical interface is `jump` |
| Tunnel subnet | `10.88.0.0/24` | Disjoint from Dromos `10.66.0.0/24` and `10.77.0.0/30` |
| S | `10.88.0.1` | |
| B (exit) | `10.88.0.2` | Exactly one exit; SSH to B at this address |
| A (clients) | `10.88.0.10` and up | Allocated by `jump-server add` |
| Listen port on S | UDP `51830` | Dromos uses 51820/51821 |
| Policy routing on S | rule pref `1880`, table `188` | Dromos uses tables 100/101 and fwmarks 0x42/0x43 |
| MTU | `1380` on peers | Leaves room for the double encapsulation |

Jump can run on the same VPS as Phebes or Peleon. It uses its own interface, port, subnet, table and rule, and it only adds firewall rules.

## How a packet flows

1. A sends a packet for `1.2.3.4` into the tunnel (`AllowedIPs = 0.0.0.0/0`).
2. S decrypts it on `dromos-jump`. The rule `iif dromos-jump lookup 188` sends it back out of `dromos-jump`.
3. WireGuard on S picks the peer with the most specific `AllowedIPs`. Clients only own their `/32`, so `1.2.3.4` matches the exit peer's `0.0.0.0/0`, which is B.
4. B decrypts it and masquerades it out of its own uplink. The internet sees B's IP.
5. The reply comes back to B. B de-NATs it to `10.88.0.10` and sends it to S, and S forwards it to A by its `/32`.

S does no NAT. B is the only place where the address changes.

## Setup order

1. **Server.** Run `sudo server/install.sh --endpoint <S public IP or DNS>`, then open UDP/51830 at the VPS provider.
2. **Peers on S.**
   ```bash
   sudo jump-server add b-exit   --role exit
   sudo jump-server add a-laptop --role client
   ```
3. **B.** Use `jump-server show b-exit > b-exit.conf` for a laptop (see [laptop-b](laptop-b/README.md)) or `--format openwrt > b-exit.env` for a router (see [router-b](router-b/README.md)).
4. **A.** Use `jump-server show a-laptop > a-laptop.conf` for a laptop (see [laptop-a](laptop-a/README.md)) or `--format openwrt > a-router.env` for a router (see [router-a](router-a/README.md)).
5. **Verify** with the checks below.

Each machine needs its own peer. Never use one config on two devices at the same time, because WireGuard identifies a peer by its key.

## Verification

| Where | Command | Expected |
|---|---|---|
| S | `sudo wg show dromos-jump` | Every peer shows a recent `latest handshake` |
| S | `sudo jump-forward status` | Rule pref 1880, `default dev dromos-jump` in table 188, FORWARD accept |
| B | `curl -4 ifconfig.me` (router: `uclient-fetch -qO- https://ifconfig.me/ip`) | B's public IP, call it `X` |
| A | `curl -4 ifconfig.me` | The same `X` |
| A | `ping 10.88.0.1` and `ping 10.88.0.2` | Both answer |
| A | `ssh user@10.88.0.2` (router B: `ssh root@10.88.0.2`) | Login to B through the tunnel |

From anywhere else you can reach B through S as a jump host:

```bash
ssh -J user@<S> user@10.88.0.2
```

## OpenWrt router behind another router

A fresh OpenWrt router uses `192.168.1.1/24` on LAN. Many home routers use the same `192.168.1.x` network, so a router plugged in behind one ends up with WAN and LAN in the same subnet. Routing then breaks in odd ways: DNS that answers only sometimes, `Operation not permitted` from `wget` or `apk`, and package installs that fail. Before you plug in WAN or run `setup.sh`, move the OpenWrt LAN to a subnet the upstream router doesn't use. For example, use `192.168.117.1` for router B and `192.168.118.1` for router A.

### In LuCI

1. Leave WAN unplugged. Connect a computer by cable to a **LAN** port and open `http://192.168.1.1`. Set a root password if LuCI asks for one.
2. Go to **Network → Interfaces** and click **Edit** on `lan`.
3. On **General Settings**, change **IPv4 address** to the new address, for example `192.168.117.1`. Keep the netmask `255.255.255.0`. The DHCP range follows the new subnet automatically.
4. Click **Save**. Don't click Save & Apply.
5. Open the drop-down next to **Save & Apply** and choose **Apply unchecked**. Plain Save & Apply waits about 90 seconds for you to confirm from the old address, which you can no longer reach, and then rolls the change back.
6. Wait about 30 seconds, then unplug and replug the computer's cable, or renew DHCP, so it gets an address in the new subnet. If it doesn't, set a static IP such as `192.168.117.10/24`.
7. Open `http://192.168.117.1`, then plug in WAN.

### From SSH

Run it as one line, because the session drops when the network restarts:

```sh
uci set network.lan.ipaddr='192.168.117.1'; uci commit network; /etc/init.d/network restart
```

### If DNS is still unreliable

If the upstream router hands out a DNS server that doesn't work, or your ISP blocks a public resolver such as `1.1.1.1`, set WAN DNS by hand. In LuCI, go to **Network → Interfaces → wan → Edit → Advanced Settings**, untick **Use DNS servers advertised by peer**, enter `8.8.8.8` and `8.8.4.4`, and click **Save & Apply**. Do the same on `wan6` if it exists. Then check that the router is online:

```sh
nslookup downloads.openwrt.org && uclient-fetch -qO- https://ifconfig.me/ip; echo
```

If your ISP blocks `1.1.1.1`, also set the clients' DNS on S (`jump-server init --dns "8.8.8.8, 8.8.4.4"`) and regenerate A's config, because A's DNS queries exit through B.

### Locked out

If you can't reach the router at either address, factory-reset it. Hold Reset for about 10 seconds while it's running, or use failsafe mode: set a static IP `192.168.1.10/24`, press Reset while the power LED blinks fast during boot, then run `ssh root@192.168.1.1` and `firstboot -y && reboot`. This restores default OpenWrt at `192.168.1.1`, not the stock vendor firmware.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| No handshake on a peer | UDP/51830 blocked at the VPS provider, wrong endpoint, or wrong key | Open the port and re-check with `jump-server show` |
| A has a handshake but no internet | B is down or has no handshake | Check `wg show` on S. If there is no exit peer, A's traffic is dropped, which is intended |
| A shows its own ISP IP | IPv6 or DNS going around the tunnel, or a router-A kill switch that is off | See the [laptop-a](laptop-a/README.md) and [router-a](router-a/README.md) notes |
| Router's DNS answers only sometimes, `wget`/`apk` fail with `Operation not permitted` | OpenWrt LAN and the upstream router's network are both `192.168.1.x` | Move the OpenWrt LAN, see [OpenWrt router behind another router](#openwrt-router-behind-another-router) |
| Small pages load but big transfers hang | MTU or PMTU problems | Lower the MTU to 1280 with `jump-server init --mtu 1280` and regenerate the peer configs |
| B works, then stops after a while | The B laptop went to sleep | Disable suspend on B, or use a router as B |
| A↔B works but S's FORWARD drops traffic | Docker or UFW reset FORWARD | Run `sudo systemctl restart wg-quick@dromos-jump`, which re-runs `jump-forward up` |

## Limits

- The tunnel is IPv4 only. IPv6 is disabled on tunnel interfaces, and router A turns off IPv6 on its LAN.
- Throughput is capped by B's upload and download, plus S's bandwidth in both directions. Latency is roughly A→S plus S→B.
- If B's ISP uses CGNAT, the internet sees the ISP's shared address. That is still B's location, just not a unique IP.
- There is one exit per hub. To have several exits, run a second hub with a different `--port`, `--subnet` and interface.
