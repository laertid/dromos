#!/usr/bin/env bash
# Install the Dromos jump hub on a Debian/Ubuntu server with a public IP.
#
#   sudo ./install.sh --endpoint <public-ip-or-dns> [--port 51830] [--subnet 10.88.0.0/24]
#
# Re-runnable: installs/updates the scripts and re-runs `jump-server init`
# with whatever flags you pass (existing keys and peers are kept).
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "run as root (sudo $0 ...)" >&2
    exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export DEBIAN_FRONTEND=noninteractive
missing=()
command -v wg >/dev/null       || missing+=(wireguard-tools)
command -v iptables >/dev/null || missing+=(iptables)
command -v python3 >/dev/null  || missing+=(python3)
command -v qrencode >/dev/null || missing+=(qrencode)
if ((${#missing[@]})); then
    apt-get update -qq
    apt-get install -y -qq "${missing[@]}"
fi

install -m 0755 "$HERE/jump-server"  /usr/local/sbin/jump-server
install -m 0755 "$HERE/jump-forward" /usr/local/sbin/jump-forward

/usr/local/sbin/jump-server init "$@"

cat <<'EOF'

Next steps:
  sudo jump-server add b-exit --role exit       # machine B (laptop or router)
  sudo jump-server add a-laptop --role client   # machine A (laptop or router)
  sudo jump-server show a-laptop                # wg-quick / WireGuard app config
  sudo jump-server show b-exit --format openwrt # env file for an OpenWrt router
Also open UDP on the listen port in your VPS provider's firewall, if it has one.
EOF
