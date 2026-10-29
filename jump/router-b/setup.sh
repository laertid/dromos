#!/bin/sh
# OpenWrt: turn this router into the jump exit (machine B).
#
#   sh setup.sh b-exit.env      # configure (idempotent, re-runnable)
#   sh setup.sh --remove        # undo everything this script added
#
# b-exit.env comes from the server:
#   sudo jump-server show b-exit --format openwrt > b-exit.env
#
# Traffic arriving from the tunnel is forwarded to WAN only and NATed by the
# stock wan zone. The tunnel cannot reach this router's LAN. SSH (22/tcp) and
# ping from the tunnel to the router itself are allowed unless JUMP_ALLOW_SSH=0.
set -eu

IF=jump

log() { echo "jump: $*"; }

pkg_install() {
    if command -v apk >/dev/null 2>&1; then
        apk update >/dev/null && apk add "$@"
    else
        opkg update >/dev/null && opkg install "$@"
    fi
}

wan_zone() {
    for s in $(uci show firewall | sed -n 's/^firewall\.\([^.=]*\)=zone$/\1/p'); do
        [ "$(uci -q get "firewall.$s.name")" = "wan" ] && { echo "$s"; return 0; }
    done
    return 1
}

remove() {
    for s in network.$IF network.${IF}_server \
             firewall.${IF}_zone firewall.${IF}_fwd_wan firewall.${IF}_ssh firewall.${IF}_ping; do
        uci -q delete "$s" || true
    done
    uci commit network
    uci commit firewall
    /etc/init.d/network reload
    /etc/init.d/firewall restart
    log "removed"
}

if [ "${1:-}" = "--remove" ]; then
    remove
    exit 0
fi

ENV="${1:?usage: $0 <exit.env> | --remove}"
# shellcheck disable=SC1090
. "$ENV"
[ "${JUMP_ROLE:-}" = "exit" ] || { echo "$ENV is not an exit env (JUMP_ROLE=${JUMP_ROLE:-unset})" >&2; exit 1; }

if ! command -v wg >/dev/null 2>&1 || [ ! -e /lib/netifd/proto/wireguard.sh ]; then
    log "installing WireGuard packages"
    pkg_install wireguard-tools luci-proto-wireguard
fi

# ---- network: tunnel interface + server peer
uci -q delete network.$IF || true
uci set network.$IF=interface
uci set network.$IF.proto='wireguard'
uci set network.$IF.private_key="$JUMP_PRIVATE_KEY"
uci add_list network.$IF.addresses="$JUMP_ADDRESS"
uci set network.$IF.mtu="$JUMP_MTU"

uci -q delete network.${IF}_server || true
uci set network.${IF}_server="wireguard_$IF"
uci set network.${IF}_server.description='dromos jump server'
uci set network.${IF}_server.public_key="$JUMP_SERVER_PUBLIC_KEY"
uci set network.${IF}_server.endpoint_host="$JUMP_ENDPOINT_HOST"
uci set network.${IF}_server.endpoint_port="$JUMP_ENDPOINT_PORT"
uci set network.${IF}_server.persistent_keepalive='25'
uci set network.${IF}_server.route_allowed_ips='1'
uci add_list network.${IF}_server.allowed_ips="$JUMP_SUBNET"

# ---- firewall: tunnel -> wan only, NAT by the wan zone
uci -q delete firewall.${IF}_zone || true
uci set firewall.${IF}_zone=zone
uci set firewall.${IF}_zone.name="$IF"
uci add_list firewall.${IF}_zone.network="$IF"
uci set firewall.${IF}_zone.input='REJECT'
uci set firewall.${IF}_zone.output='ACCEPT'
uci set firewall.${IF}_zone.forward='REJECT'
uci set firewall.${IF}_zone.mtu_fix='1'

uci -q delete firewall.${IF}_fwd_wan || true
uci set firewall.${IF}_fwd_wan=forwarding
uci set firewall.${IF}_fwd_wan.src="$IF"
uci set firewall.${IF}_fwd_wan.dest='wan'

uci -q delete firewall.${IF}_ssh || true
uci -q delete firewall.${IF}_ping || true
if [ "${JUMP_ALLOW_SSH:-1}" = "1" ]; then
    uci set firewall.${IF}_ssh=rule
    uci set firewall.${IF}_ssh.name='Allow-SSH-from-jump'
    uci set firewall.${IF}_ssh.src="$IF"
    uci set firewall.${IF}_ssh.proto='tcp'
    uci set firewall.${IF}_ssh.dest_port='22'
    uci set firewall.${IF}_ssh.target='ACCEPT'

    uci set firewall.${IF}_ping=rule
    uci set firewall.${IF}_ping.name='Allow-Ping-from-jump'
    uci set firewall.${IF}_ping.src="$IF"
    uci set firewall.${IF}_ping.proto='icmp'
    uci set firewall.${IF}_ping.icmp_type='echo-request'
    uci set firewall.${IF}_ping.target='ACCEPT'
fi

WAN_ZONE="$(wan_zone)" || { echo "no firewall zone named 'wan'" >&2; exit 1; }
uci set "firewall.$WAN_ZONE.masq=1"

uci commit network
uci commit firewall
/etc/init.d/network reload
/etc/init.d/firewall restart

DB_IF="$(uci -q get dropbear.@dropbear[0].Interface || true)"
if [ -n "$DB_IF" ] && [ "$DB_IF" != "$IF" ]; then
    log "note: dropbear only listens on '$DB_IF'; SSH over the tunnel needs:"
    log "  uci delete dropbear.@dropbear[0].Interface; uci commit dropbear; /etc/init.d/dropbear restart"
fi

sleep 5
wg show "$IF" || true
log "exit configured at ${JUMP_ADDRESS%/*}; WAN IP: $(uclient-fetch -qO- https://ifconfig.me 2>/dev/null || echo unknown)"
