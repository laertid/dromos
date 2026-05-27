#!/bin/sh
# Phebes lab init.  Networks the same as docker-compose.yml.
#
# We discover interfaces by IP, not by name, because Docker numbers them
# nondeterministically (eth0, eth1, eth2 in some order).
#
set -eu

PHEBES_CLIENT_IP=172.30.10.4
PHEBES_BACKBONE_IP=172.30.20.4
PHEBES_EGRESS_IP=172.30.30.40
PELEON_BACKBONE_IP=172.30.20.3

ifname_for_ip() {
    # echoes the interface holding $1, or empty
    ip -o -4 addr show | awk -v want="$1" '$4 ~ ("^"want"/") {print $2; exit}'
}

CLIENT_IF=$(ifname_for_ip "$PHEBES_CLIENT_IP")
BACKBONE_IF=$(ifname_for_ip "$PHEBES_BACKBONE_IP")
EGRESS_IF=$(ifname_for_ip "$PHEBES_EGRESS_IP")

echo "[phebes] client=$CLIENT_IF backbone=$BACKBONE_IF egress=$EGRESS_IF"
test -n "$CLIENT_IF" -a -n "$BACKBONE_IF" -a -n "$EGRESS_IF"

echo "[phebes] enable forwarding (best-effort; compose sysctls already did it)"
sysctl -w net.ipv4.ip_forward=1 || true
sysctl -w net.ipv4.conf.all.rp_filter=0 || true

echo "[phebes] policy routing tables (numeric IDs)"
# Table 100 ("RU direct"): exit out the egress network.  We pick the gateway
# on that specific network rather than 'ip route show default' which may
# point at the wrong bridge in a multi-network setup.
EGRESS_NET=$(ip -o -4 addr show dev "$EGRESS_IF" | awk '{print $4}' | head -1)
EGRESS_GW=$(ip route show "$EGRESS_NET" dev "$EGRESS_IF" 2>/dev/null \
            | awk '/proto kernel/ {next} /scope link/ {next} {print}' \
            | head -1)
# If no explicit gw on that net, derive .1 of the subnet (docker bridge default).
if [ -z "$EGRESS_GW" ]; then
    EGRESS_GW=$(echo "$EGRESS_NET" | awk -F/ '{split($1,a,"."); print a[1]"."a[2]"."a[3]".1"}')
fi
echo "[phebes] egress gw = $EGRESS_GW on $EGRESS_IF ($EGRESS_NET)"
ip route replace default via "$EGRESS_GW" dev "$EGRESS_IF" table 100
# Table 101: foreign, default via Peleon's backbone IP
ip route replace default via "$PELEON_BACKBONE_IP" dev "$BACKBONE_IF" table 101

ip rule add fwmark 0x42 lookup 100 2>/dev/null || true
ip rule add fwmark 0x43 lookup 101 2>/dev/null || true

echo "[phebes] install nft table inet dromos"
# RU set: 172.30.30.10 only.  Everything else is "foreign".
nft -f - <<EOF
table inet dromos {
    set dromos_ru4 {
        type ipv4_addr
        flags interval
        elements = { 172.30.30.10/32 }
    }
    chain mark_clients {
        type filter hook prerouting priority mangle; policy accept;
        iifname "$CLIENT_IF" ip daddr @dromos_ru4 counter meta mark set 0x42 return
        iifname "$CLIENT_IF"                       counter meta mark set 0x43
    }
    chain forward {
        type filter hook forward priority 0; policy accept;
        iifname "$CLIENT_IF" oifname { "$EGRESS_IF", "$BACKBONE_IF" } counter accept
        iifname { "$EGRESS_IF", "$BACKBONE_IF" } oifname "$CLIENT_IF" ct state established,related counter accept
    }
    chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        iifname "$CLIENT_IF" oifname "$EGRESS_IF"   meta mark 0x42 counter masquerade
        iifname "$CLIENT_IF" oifname "$BACKBONE_IF" meta mark 0x43 counter masquerade
    }
}
EOF

echo "[phebes] ready"
echo "$CLIENT_IF $BACKBONE_IF $EGRESS_IF" > /tmp/dromos-ifaces
exec tail -f /dev/null
