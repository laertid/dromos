#!/usr/bin/env bash
# Linux helper for machine A (laptop). macOS/Windows/phones: import the file
# into the official WireGuard app instead (see README.md).
#
#   sudo ./install.sh a-laptop.conf          # install, do not start
#   sudo ./install.sh a-laptop.conf --boot   # install and start at boot
#   sudo ./install.sh --remove
#
# Then: sudo wg-quick up dromos-jump / sudo wg-quick down dromos-jump
set -euo pipefail

IF=dromos-jump
CONF=/etc/wireguard/$IF.conf

if [[ $EUID -ne 0 ]]; then
    echo "run as root (sudo $0 ...)" >&2
    exit 1
fi

if [[ "${1:-}" == "--remove" ]]; then
    systemctl disable "wg-quick@$IF" 2>/dev/null || true
    wg-quick down "$IF" 2>/dev/null || true
    rm -f "$CONF"
    echo "removed $IF"
    exit 0
fi

SRC="${1:?usage: $0 <client.conf> [--boot] | --remove}"
grep -q '^AllowedIPs *= *0.0.0.0/0' "$SRC" || {
    echo "$SRC is not a client config (generate it with: jump-server show <client-name>)" >&2
    exit 1
}

if ! command -v wg-quick >/dev/null; then
    if command -v apt-get >/dev/null; then
        apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wireguard-tools resolvconf
    elif command -v dnf >/dev/null; then
        dnf install -y -q wireguard-tools
    elif command -v pacman >/dev/null; then
        pacman -S --noconfirm --needed wireguard-tools openresolv
    else
        echo "install wireguard-tools manually" >&2
        exit 1
    fi
fi

install -m 0600 "$SRC" "$CONF"
if [[ "${2:-}" == "--boot" ]]; then
    systemctl enable --now "wg-quick@$IF"
else
    echo "installed $CONF; start with: sudo wg-quick up $IF"
fi
