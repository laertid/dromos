#!/usr/bin/env bash
# Turn a Linux laptop/box into the jump exit (machine B).
#
#   sudo ./install.sh b-exit.conf          # install + enable at boot
#   sudo ./install.sh --remove             # stop and remove
#
# b-exit.conf comes from the server: `sudo jump-server show b-exit > b-exit.conf`
set -euo pipefail

IF=dromos-jump
CONF=/etc/wireguard/$IF.conf
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "run as root (sudo $0 ...)" >&2
    exit 1
fi

if [[ "${1:-}" == "--remove" ]]; then
    systemctl disable --now "wg-quick@$IF" 2>/dev/null || true
    rm -f "$CONF" /usr/local/sbin/jump-exit-nat
    echo "removed $IF"
    exit 0
fi

SRC="${1:?usage: $0 <exit.conf> | --remove}"
grep -q 'jump-exit-nat up' "$SRC" || {
    echo "$SRC is not an exit config (generate it with: jump-server show <exit-name>)" >&2
    exit 1
}

install_pkgs() {
    if command -v apt-get >/dev/null; then
        apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@"
    elif command -v dnf >/dev/null; then
        dnf install -y -q "$@"
    elif command -v pacman >/dev/null; then
        pacman -S --noconfirm --needed "$@"
    else
        echo "install manually: $*" >&2
        exit 1
    fi
}
pkgs=()
command -v wg >/dev/null       || pkgs+=(wireguard-tools)
command -v iptables >/dev/null || pkgs+=(iptables)
((${#pkgs[@]} == 0)) || install_pkgs "${pkgs[@]}"

install -m 0755 "$HERE/jump-exit-nat" /usr/local/sbin/jump-exit-nat
install -m 0600 "$SRC" "$CONF"

systemctl enable "wg-quick@$IF"
systemctl restart "wg-quick@$IF"
sleep 3
wg show "$IF"
echo
echo "B is up as tunnel address $(awk -F'= *' '/^Address/{print $2}' "$CONF")."
echo "Keep this machine awake: suspend stops the exit (see README)."
