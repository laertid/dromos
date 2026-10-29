# Router B: an OpenWrt router as the exit

An OpenWrt router in city B dials out to S and NATs A's traffic out of its WAN, so city B does not need an always-on PC. Target: OpenWrt 24.10/25.12 (fw4, `apk` or `opkg`). For the ASUS RT-AX52, flash OpenWrt first ([rt-ax52-openwrt.md](../rt-ax52-openwrt.md)).

The stock firmware on consumer routers, including the ASUS VPN Fusion client, can put the router's own LAN into a tunnel, but it cannot NAT traffic that arrives from the tunnel. That is why the exit role needs OpenWrt.

## 1. Create the exit peer on S

```bash
sudo jump-server add b-exit --role exit
sudo jump-server show b-exit --format openwrt > b-exit.env
```

## 2. Copy and run

```bash
scp -O b-exit.env jump/router-b/setup.sh root@192.168.1.1:/tmp/
ssh root@192.168.1.1 'sh /tmp/setup.sh /tmp/b-exit.env && rm /tmp/b-exit.env'
```

`-O` forces the legacy scp protocol, because OpenWrt's dropbear has no SFTP server by default. The script is idempotent: re-run it after regenerating `b-exit.env`.

## What `setup.sh` configures

| UCI section | Settings |
|---|---|
| `network.jump` | `proto wireguard`, `10.88.0.2/24`, MTU 1380 |
| `network.jump_server` | Peer S: endpoint, keepalive 25, `allowed_ips 10.88.0.0/24`, `route_allowed_ips 1` |
| `firewall.jump_zone` | Zone `jump`: input/forward REJECT, output ACCEPT, MSS clamping |
| `firewall.jump_fwd_wan` | Forwarding `jump → wan`. NAT comes from the stock `wan` zone (`masq 1` is enforced) |
| `firewall.jump_ssh`, `firewall.jump_ping` | SSH and ping to the router from the tunnel (set `JUMP_ALLOW_SSH=0` to skip) |

Only `10.88.0.0/24` is routed into the tunnel, so the router and its LAN keep using the local ISP as before. There is no `jump → lan` forwarding, so A cannot reach devices in B's home network.

## 3. Check

On the router:

```bash
wg show jump                                   # latest handshake with S
uclient-fetch -qO- https://ifconfig.me; echo   # B's public IP
```

On A, `curl -4 ifconfig.me` must print the same address.

## SSH into router B

Run `ssh root@10.88.0.2` from A, or `ssh -J user@<S> root@10.88.0.2` from anywhere. If `dropbear` is bound to `lan` only, `setup.sh` prints the command that unbinds it. Use keys: put your public key into `/etc/dropbear/authorized_keys` and set `uci set dropbear.@dropbear[0].PasswordAuth=off`.

## LuCI equivalent

1. Network → Interfaces → Add new interface: name `jump`, protocol WireGuard VPN. Paste the private key and set the IP `10.88.0.2/24`.
2. On the Peers tab, add a peer: the server public key, endpoint host and port, Allowed IPs `10.88.0.0/24`, check Route Allowed IPs, keepalive `25`.
3. Firewall: create zone `jump` (input reject, forward reject) and allow forward to destination zone `wan`. Make sure `wan` has Masquerading checked.

## Remove

```bash
ssh root@192.168.1.1 'sh /tmp/setup.sh --remove'
```
