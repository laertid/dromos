#!/bin/bash
# Driver for the Dromos integration lab.
#
# Brings up the compose stack, runs assertions, tears down.  Exit non-zero on
# any failure.  Uses a small set of explicit nft-counter and packet-capture
# checks so that "RU goes direct, foreign goes via Peleon" is provable.
#
set -euo pipefail
cd "$(dirname "$0")"

if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
else
    echo "neither 'docker compose' nor 'docker-compose' is installed" >&2
    exit 2
fi

cleanup() {
    echo "--- teardown ---"
    $DC down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "--- bring up lab ---"
$DC up -d >/dev/null

echo "--- wait for phebes nft to be ready ---"
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
    if docker exec dromos-phebes nft list table inet dromos >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

echo "--- pre-test snapshot on phebes ---"
docker exec dromos-phebes ip rule show
docker exec dromos-phebes ip route show table 100
docker exec dromos-phebes ip route show table 101
docker exec dromos-phebes nft list table inet dromos | head -40

echo "--- run client reachability assertions ---"
docker exec dromos-client sh /assertions.sh

echo "--- start tcpdump on Peleon backbone iface ---"
BACKBONE_IF=$(docker exec dromos-peleon sh -c '
    ip -o -4 addr show | awk "\$4 ~ /^172\.30\.20\.30\\//{print \$2; exit}"')
EGRESS_IF=$(docker exec dromos-peleon sh -c '
    ip -o -4 addr show | awk "\$4 ~ /^172\.30\.30\.30\\//{print \$2; exit}"')
echo "[lab] peleon backbone=$BACKBONE_IF egress=$EGRESS_IF"

# Capture traffic to foreign-dst at peleon egress.  If we see syn/ack
# entering or leaving here, foreign traffic actually traversed Peleon.
docker exec -d dromos-peleon sh -c \
    "tcpdump -ni $EGRESS_IF -c 4 -tnn ip and host 172.30.30.20 > /tmp/peleon-foreign.pcap 2>&1 || true"

# Capture IP traffic to RU dst at Peleon egress; should be ABSENT.  ARP
# beacons that the OS emits for the bridge are filtered out by 'ip and'.
docker exec -d dromos-peleon sh -c \
    "tcpdump -ni $EGRESS_IF -c 4 -tnn ip and host 172.30.30.10 > /tmp/peleon-ru.pcap 2>&1 || true"

sleep 1

echo "--- fire test requests ---"
docker exec dromos-client curl -sS --max-time 4 http://172.30.30.10/ -o /dev/null
docker exec dromos-client curl -sS --max-time 4 http://172.30.30.20/ -o /dev/null
sleep 2

# Stop any leftover tcpdumps
docker exec dromos-peleon pkill -f tcpdump 2>/dev/null || true

echo "--- peleon captures ---"
echo "[foreign on peleon egress]:"
docker exec dromos-peleon cat /tmp/peleon-foreign.pcap || true
echo "[ru on peleon egress]:"
docker exec dromos-peleon cat /tmp/peleon-ru.pcap || true

echo "--- nft counters on phebes ---"
docker exec dromos-phebes nft list table inet dromos

# Pull mark-stanza packet counts via JSON for reliability
mark_counts=$(docker exec dromos-phebes nft -j list chain inet dromos mark_clients 2>/dev/null)
ru_pkts=$(echo "$mark_counts"     | python3 -c 'import sys,json; d=json.load(sys.stdin);
exprs=[r for r in d["nftables"] if "rule" in r];
out=0
for r in exprs:
  for e in r["rule"]["expr"]:
    if isinstance(e,dict) and "mangle" in e:
      val=e["mangle"]["value"]
      if val==66 or val=="0x42":  # 0x42==66
        for e2 in r["rule"]["expr"]:
          if "counter" in e2:
            out+=e2["counter"]["packets"]
print(out)' 2>/dev/null || echo 0)
foreign_pkts=$(echo "$mark_counts" | python3 -c 'import sys,json; d=json.load(sys.stdin);
exprs=[r for r in d["nftables"] if "rule" in r];
out=0
for r in exprs:
  for e in r["rule"]["expr"]:
    if isinstance(e,dict) and "mangle" in e:
      val=e["mangle"]["value"]
      if val==67 or val=="0x43":
        for e2 in r["rule"]["expr"]:
          if "counter" in e2:
            out+=e2["counter"]["packets"]
print(out)' 2>/dev/null || echo 0)
echo "Phebes mark counters:  RU=$ru_pkts  FOREIGN=$foreign_pkts"

fails=0
if [ "${ru_pkts:-0}" -lt 1 ]; then
    echo "FAIL: no RU-marked packets seen on Phebes" >&2
    fails=$((fails+1))
fi
if [ "${foreign_pkts:-0}" -lt 1 ]; then
    echo "FAIL: no FOREIGN-marked packets seen on Phebes" >&2
    fails=$((fails+1))
fi

if ! docker exec dromos-peleon grep -q '172.30.30.20' /tmp/peleon-foreign.pcap; then
    echo "FAIL: foreign-bound traffic did not transit Peleon" >&2
    fails=$((fails+1))
fi

if docker exec dromos-peleon grep -E 'IP 172\.30\.30\.10\.|IP .*\.> 172\.30\.30\.10\.' /tmp/peleon-ru.pcap >/dev/null 2>&1; then
    echo "FAIL: RU-bound IP traffic leaked through Peleon" >&2
    fails=$((fails+1))
fi

if [ "$fails" -gt 0 ]; then
    echo ""
    echo "=================="
    echo "LAB: $fails FAILURES"
    echo "=================="
    exit 1
fi

echo ""
echo "=================="
echo "LAB: ALL TESTS PASS"
echo "=================="
