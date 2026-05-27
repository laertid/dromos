#!/bin/sh
# Peleon lab init.  Forwards anything from backbone net out the egress net.
set -eu

PELEON_BACKBONE_IP=172.30.20.3
PELEON_EGRESS_IP=172.30.30.30

ifname_for_ip() {
    ip -o -4 addr show | awk -v want="$1" '$4 ~ ("^"want"/") {print $2; exit}'
}

BACKBONE_IF=$(ifname_for_ip "$PELEON_BACKBONE_IP")
EGRESS_IF=$(ifname_for_ip "$PELEON_EGRESS_IP")
echo "[peleon] backbone=$BACKBONE_IF egress=$EGRESS_IF"
test -n "$BACKBONE_IF" -a -n "$EGRESS_IF"

sysctl -w net.ipv4.ip_forward=1 || true
sysctl -w net.ipv4.conf.all.rp_filter=0 || true

echo "[peleon] install nft table inet dromos"
nft -f - <<EOF
table inet dromos {
    chain forward {
        type filter hook forward priority 0; policy accept;
        iifname "$BACKBONE_IF" oifname "$EGRESS_IF" counter accept
        iifname "$EGRESS_IF" oifname "$BACKBONE_IF" ct state established,related counter accept
    }
    chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        iifname "$BACKBONE_IF" oifname "$EGRESS_IF" counter masquerade
    }
}
EOF

echo "[peleon] ready"
exec tail -f /dev/null
