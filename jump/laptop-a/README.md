# Laptop A: send one device's traffic out via B

A laptop (or a phone) in city A runs a normal full-tunnel WireGuard client. Nothing on it is special to jump. The server decides that its traffic exits via B.

## 1. Create the peer on S

```bash
sudo jump-server add a-laptop --role client
sudo jump-server show a-laptop > a-laptop.conf    # or: --qr for a phone
```

Copy `a-laptop.conf` to the laptop over SSH. It contains the laptop's private key.

## 2. Install

| OS | Steps |
|---|---|
| Linux | `sudo ./install.sh a-laptop.conf`, then `sudo wg-quick up dromos-jump`. Add `--boot` to start at boot. |
| macOS / Windows | Install the official WireGuard app, choose Import tunnel(s) from file, select `a-laptop.conf`, then Activate. |
| Android / iOS | WireGuard app, then + and Scan from QR code, using the output of `jump-server show a-phone --qr`. |

The config uses `AllowedIPs = 0.0.0.0/0`, sets DNS to `1.1.1.1, 1.0.0.1` (these queries also exit via B) and `MTU = 1380`. The client adds the exception route to S automatically.

## 3. Check

```bash
curl -4 ifconfig.me        # must print B's public IP
ping 10.88.0.2             # B
ssh user@10.88.0.2         # B through the tunnel
```

## Notes

- **IPv6.** The tunnel is IPv4 only. If the local network gives the laptop IPv6, IPv6-capable sites can bypass the tunnel. To prevent this, either disable IPv6 on the laptop's network adapter, or add `::/0` to `AllowedIPs` in the file. That sends IPv6 into the tunnel, where it is dropped, so apps fall back to IPv4.
- **Kill switch.** On Windows, the app's "Block untunneled traffic" option (shown for `0.0.0.0/0` configs) stops leaks when the tunnel drops. On macOS, enable "On-demand" so the tunnel reconnects.
- **Linux DNS.** `wg-quick` needs `resolvconf` (Debian/Ubuntu) or `openresolv` to apply the `DNS =` line. `install.sh` installs it with `wireguard-tools`.
- **Disconnect.** Run `sudo wg-quick down dromos-jump`, or toggle the tunnel off in the app. To remove it completely, run `sudo ./install.sh --remove`.
