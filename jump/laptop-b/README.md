# Laptop B: a Linux machine as the exit

A Linux laptop, mini PC or Raspberry Pi in city B dials out to S and NATs everything that A sends through the tunnel out of its own uplink. Websites then see B's public IP.

## 1. Create the exit peer on S

```bash
sudo jump-server add b-exit --role exit
sudo jump-server show b-exit > b-exit.conf
```

Copy the folder `jump/laptop-b/` and `b-exit.conf` to B.

## 2. Install

```bash
sudo ./install.sh b-exit.conf
```

This script:

1. Installs `wireguard-tools` and `iptables` if needed (apt, dnf or pacman).
2. Installs `jump-exit-nat` to `/usr/local/sbin/`.
3. Installs the config to `/etc/wireguard/dromos-jump.conf` and enables `wg-quick@dromos-jump`, so it starts at boot.

The tunnel gets `10.88.0.2/24`, and only the tunnel subnet is routed into it. B's own traffic keeps using its normal internet connection.

## What `jump-exit-nat` does

It runs on PostUp and PreDown, is idempotent, and tags every rule with the comment `dromos-jump-exit`:

```
sysctl net.ipv4.ip_forward=1
iptables -I FORWARD -i dromos-jump -d 10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,169.254.0.0/16 -j DROP
iptables -I FORWARD -i dromos-jump -j ACCEPT
iptables -I FORWARD -o dromos-jump -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
iptables -t nat -I POSTROUTING -s 10.88.0.0/24 ! -o dromos-jump -j MASQUERADE
```

- The DROP rule keeps A out of B's home LAN. To let A reach it, run `sudo systemctl edit wg-quick@dromos-jump`, add `[Service]` and `Environment=JUMP_ALLOW_LAN=1`, then restart the unit.
- MASQUERADE matches "not the tunnel" instead of a fixed interface name, so it keeps working when the laptop switches between Wi-Fi and Ethernet.
- The rules are inserted at the top of FORWARD, so they take effect even when Docker or UFW set the FORWARD policy to DROP.

Inspect with `sudo jump-exit-nat status dromos-jump 10.88.0.0/24`.

## 3. Check

```bash
sudo wg show dromos-jump     # latest handshake with S
curl -4 ifconfig.me          # B's public IP; A must show the same
```

## SSH into B

From A, or from any jump client, run `ssh user@10.88.0.2`. From anywhere else, run `ssh -J user@<S> user@10.88.0.2`.

Harden sshd on B with `PasswordAuthentication no`. To accept SSH only from the tunnel, add `ListenAddress 10.88.0.2` and make sure `ssh.service` starts after `wg-quick@dromos-jump`.

## Keep it running

- **No sleep.** Disable suspend (`sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target`) and set lid close to `ignore` in `/etc/systemd/logind.conf` (`HandleLidSwitch=ignore`).
- **Power and network.** Use a cable if possible. Keepalive (25 s) keeps the home NAT mapping open.

## Remove

```bash
sudo ./install.sh --remove
```
