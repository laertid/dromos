#!/bin/sh
# OpenWrt: send all LAN traffic of this router through the jump tunnel (machine A).
#
#   sh setup.sh a-router.env    # configure (idempotent, re-runnable)
#   sh setup.sh --remove        # undo everything this script changed
#
# a-router.env comes from the server:
#   sudo jump-server show a-router --format openwrt > a-router.env
#
# Design:
#   - Only traffic *from LAN* is policy-routed into the tunnel (ip rule
#     "in lan lookup 188"). The router's own traffic, including the WireGuard
#     handshake itself, keeps using WAN, so there is no endpoint-route loop.
#   - LAN is NATed into the tunnel, so the server only needs this router's /32.
#   - Kill switch (JUMP_KILL_SWITCH=1, default): the stock lan->wan forwarding
#     is disabled, so LAN devices never fall back to the local ISP.
#   - dnsmasq upstream is switched to JUMP_DNS and those resolvers are routed
#     via the tunnel, so DNS also exits from B.
#   - IPv6 on LAN is turned off (JUMP_DISABLE_IPV6=1, default) because the
#     tunnel is IPv4-only.
set -eu

IF=jump
TABLE=188
PREF=1880
STATE=/etc/dromos-jump.state

log() { echo "jump: $*"; }

pkg_install() {
    if command -v apk >/dev/null 2>&1; then
        apk update >/dev/null && apk add "$@"
    else
        opkg update >/dev/null && opkg install "$@"
    fi
}

lan_wan_forwardings() {
    for s in $(uci show firewall | sed -n 's/^firewall\.\([^.=]*\)=forwarding$/\1/p'); do
        [ "$(uci -q get "firewall.$s.src")" = "lan" ] && [ "$(uci -q get "firewall.$s.dest")" = "wan" ] && echo "$s"
    done
}

delete_jump_sections() {
    for s in $(uci show network | sed -n "s/^network\.\(${IF}_[^.=]*\)=.*/\1/p") $IF; do
        uci -q delete "network.$s" || true
    done
    for s in ${IF}_zone ${IF}_fwd_lan; do
        uci -q delete "firewall.$s" || true
    done
}

remove() {
    delete_jump_sections
    # Re-enable lan->wan and restore DNS/IPv6 as recorded at setup time.
    for s in $(lan_wan_forwardings); do uci -q delete "firewall.$s.enabled" || true; done
    if [ -f "$STATE" ]; then
        # shellcheck disable=SC1090
        . "$STATE"
        for d in ${JUMP_DNS:-}; do uci -q del_list dhcp.@dnsmasq[0].server="$d" || true; done
        [ "${OLD_NORESOLV:-}" = "1" ] || uci -q delete dhcp.@dnsmasq[0].noresolv || true
        if [ "${IPV6_CHANGED:-0}" = "1" ]; then
            uci set dhcp.lan.ra="${OLD_RA:-server}"
            uci set dhcp.lan.dhcpv6="${OLD_DHCPV6:-server}"
            uci set network.lan.ip6assign="${OLD_IP6ASSIGN:-60}"
        fi
        rm -f "$STATE"
    fi
    uci commit network; uci commit firewall; uci commit dhcp
    /etc/init.d/network reload
    /etc/init.d/firewall restart
    /etc/init.d/dnsmasq restart
    log "removed; LAN uses the local ISP again"
}

if [ "${1:-}" = "--remove" ]; then
    remove
    exit 0
fi

ENV="${1:?usage: $0 <client.env> | --remove}"
# shellcheck disable=SC1090
. "$ENV"
[ "${JUMP_ROLE:-}" = "client" ] || { echo "$ENV is not a client env (JUMP_ROLE=${JUMP_ROLE:-unset})" >&2; exit 1; }
KILL_SWITCH="${JUMP_KILL_SWITCH:-1}"
DISABLE_IPV6="${JUMP_DISABLE_IPV6:-1}"

if ! command -v wg >/dev/null 2>&1 || [ ! -e /lib/netifd/proto/wireguard.sh ]; then
    log "installing WireGuard packages"
    pkg_install wireguard-tools luci-proto-wireguard
fi

# Record pre-existing DNS/IPv6 settings once, so --remove can restore them.
if [ ! -f "$STATE" ]; then
    {
        echo "JUMP_DNS='$JUMP_DNS'"
        echo "OLD_NORESOLV='$(uci -q get dhcp.@dnsmasq[0].noresolv || true)'"
        echo "OLD_RA='$(uci -q get dhcp.lan.ra || true)'"
        echo "OLD_DHCPV6='$(uci -q get dhcp.lan.dhcpv6 || true)'"
        echo "OLD_IP6ASSIGN='$(uci -q get network.lan.ip6assign || true)'"
        echo "IPV6_CHANGED='$DISABLE_IPV6'"
    } > "$STATE"
fi

delete_jump_sections

# ---- network: tunnel interface + server peer (no automatic routes)
uci set network.$IF=interface
uci set network.$IF.proto='wireguard'
uci set network.$IF.private_key="$JUMP_PRIVATE_KEY"
uci add_list network.$IF.addresses="$JUMP_ADDRESS"
uci set network.$IF.mtu="$JUMP_MTU"

uci set network.${IF}_server="wireguard_$IF"
uci set network.${IF}_server.description='dromos jump server'
uci set network.${IF}_server.public_key="$JUMP_SERVER_PUBLIC_KEY"
uci set network.${IF}_server.endpoint_host="$JUMP_ENDPOINT_HOST"
uci set network.${IF}_server.endpoint_port="$JUMP_ENDPOINT_PORT"
uci set network.${IF}_server.persistent_keepalive='25'
uci set network.${IF}_server.route_allowed_ips='0'
uci add_list network.${IF}_server.allowed_ips='0.0.0.0/0'

# ---- policy routing: LAN -> table 188 -> tunnel
uci set network.${IF}_default=route
uci set network.${IF}_default.interface="$IF"
uci set network.${IF}_default.target='0.0.0.0/0'
uci set network.${IF}_default.table="$TABLE"

uci set network.${IF}_rule=rule
uci set network.${IF}_rule.in='lan'
uci set network.${IF}_rule.lookup="$TABLE"
uci set network.${IF}_rule.priority="$PREF"

# ---- DNS: router's dnsmasq resolves through the tunnel
n=0
for d in $JUMP_DNS; do
    n=$((n + 1))
    uci set network.${IF}_dns$n=route
    uci set network.${IF}_dns$n.interface="$IF"
    uci set network.${IF}_dns$n.target="$d/32"
    uci -q del_list dhcp.@dnsmasq[0].server="$d" || true
    uci add_list dhcp.@dnsmasq[0].server="$d"
done
uci set dhcp.@dnsmasq[0].noresolv='1'

# ---- IPv6 off on LAN (tunnel is IPv4-only)
if [ "$DISABLE_IPV6" = "1" ]; then
    uci set dhcp.lan.ra='disabled'
    uci set dhcp.lan.dhcpv6='disabled'
    uci -q delete network.lan.ip6assign || true
fi

# ---- firewall: lan -> jump with NAT; optional kill switch on lan -> wan
uci set firewall.${IF}_zone=zone
uci set firewall.${IF}_zone.name="$IF"
uci add_list firewall.${IF}_zone.network="$IF"
uci set firewall.${IF}_zone.input='REJECT'
uci set firewall.${IF}_zone.output='ACCEPT'
uci set firewall.${IF}_zone.forward='REJECT'
uci set firewall.${IF}_zone.masq='1'
uci set firewall.${IF}_zone.mtu_fix='1'

uci set firewall.${IF}_fwd_lan=forwarding
uci set firewall.${IF}_fwd_lan.src='lan'
uci set firewall.${IF}_fwd_lan.dest="$IF"

for s in $(lan_wan_forwardings); do
    if [ "$KILL_SWITCH" = "1" ]; then
        uci set "firewall.$s.enabled=0"
    else
        uci -q delete "firewall.$s.enabled" || true
    fi
done

uci commit network; uci commit firewall; uci commit dhcp
/etc/init.d/network reload
/etc/init.d/firewall restart
/etc/init.d/dnsmasq restart

sleep 5
wg show "$IF" || true
log "LAN now exits via the jump tunnel (kill switch: $KILL_SWITCH)"
log "check from a LAN device: curl ifconfig.me  -> should print B's public IP"
