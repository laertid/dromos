# Server S: the jump hub

S needs a public IPv4 address and Ubuntu or Debian. It can be the same VPS as Phebes or Peleon, because jump does not touch Dromos interfaces, tables, marks or nftables.

## Files

| File | Installed to | Purpose |
|---|---|---|
| `install.sh` | | Installs packages and scripts, then runs `jump-server init` |
| `jump-server` | `/usr/local/sbin/jump-server` | Hub and peer management, and config generation for every part |
| `jump-forward` | `/usr/local/sbin/jump-forward` | wg-quick PostUp/PreDown: policy rule, table 188, FORWARD accept, sysctls |

State is kept in `/etc/dromos/jump/jump.json` and `jump.env`, and keys in `/etc/dromos/secrets/jump/` (mode 0700). The server config is `/etc/wireguard/dromos-jump.conf`, and the unit is `wg-quick@dromos-jump`.

## Install

```bash
git clone git@github.com:laertid/dromos.git && cd dromos/jump/server
sudo ./install.sh --endpoint <public-ip-or-dns>
```

Optional flags (also accepted later by `jump-server init`):

| Flag | Default |
|---|---|
| `--port` | `51830` |
| `--subnet` | `10.88.0.0/24` (can only be changed while there are no peers) |
| `--dns` | `1.1.1.1, 1.0.0.1` (written into client configs; resolved via B) |
| `--mtu` | `1380` (written into peer configs) |

Then open **UDP/51830** in the VPS provider's firewall or security group, if it has one. If UFW is active on the host, `init` adds only `allow` rules: SSH, the UDP port, and `route allow in on dromos-jump out on dromos-jump`. It never resets UFW or changes its default policies.

## Manage peers

```bash
sudo jump-server add b-exit --role exit        # machine B, always 10.88.0.2, only one allowed
sudo jump-server add a-laptop --role client    # machine A, 10.88.0.10 and up
sudo jump-server add a-router --role client    # a router in city A is just another client
sudo jump-server list
sudo jump-server remove a-laptop
```

Peers are applied live with `wg syncconf`, so existing tunnels are not interrupted.

## Hand out configs

| Target | Command |
|---|---|
| Laptop A (any OS) | `sudo jump-server show a-laptop > a-laptop.conf` |
| Phone A | `sudo jump-server show a-phone --qr` |
| Router A (OpenWrt) | `sudo jump-server show a-router --format openwrt > a-router.env` |
| Laptop B (Linux) | `sudo jump-server show b-exit > b-exit.conf` |
| Router B (OpenWrt) | `sudo jump-server show b-exit --format openwrt > b-exit.env` |

These files contain private keys. Copy them over SSH (for example `scp`), not through chat.

## What gets installed in the kernel

`jump-forward up dromos-jump` runs on every interface start and is idempotent:

```
ip rule add pref 1880 iif dromos-jump lookup 188
ip route replace default dev dromos-jump table 188
iptables -I FORWARD 1 -i dromos-jump -o dromos-jump -j ACCEPT   # Docker sets FORWARD policy DROP
sysctl net.ipv4.ip_forward=1
sysctl net.ipv4.conf.dromos-jump.rp_filter=2                    # replies from B carry internet source IPs
```

`Table = off` keeps wg-quick from adding the exit peer's `0.0.0.0/0` to the main table, so the server's own traffic and SSH are never routed through B. `jump-forward down` removes exactly these entries.

Inspect with `sudo jump-forward status`.

## Uninstall

```bash
sudo systemctl disable --now wg-quick@dromos-jump
sudo rm -rf /etc/wireguard/dromos-jump.conf /etc/dromos/jump /etc/dromos/secrets/jump \
            /usr/local/sbin/jump-server /usr/local/sbin/jump-forward
sudo ufw delete allow 51830/udp 2>/dev/null; sudo ufw route delete allow in on dromos-jump out on dromos-jump 2>/dev/null
```
