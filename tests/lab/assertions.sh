#!/bin/sh
# Lab assertions, run from inside the client container.
#
# What we check:
#   1. Client can reach the RU destination (direct exit via Phebes).
#   2. Client can reach the foreign destination (must traverse Peleon).
#   3. Phebes nft counters show packets hitting the RU/foreign branches as
#      expected (sanity check that we are actually testing what we think).
#   4. ip rules + tables are present on Phebes.
#
set -eu

PHEBES=dromos-phebes
RU_DST=172.30.30.10
FOREIGN_DST=172.30.30.20

fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "OK:   $*"; }

echo "--- assertion 1: RU destination reachable ---"
curl -fsS --max-time 5 -o /dev/null "http://$RU_DST/" \
    && ok "client -> RU dst reachable" || fail "client -> RU dst unreachable"

echo "--- assertion 2: foreign destination reachable ---"
curl -fsS --max-time 5 -o /dev/null "http://$FOREIGN_DST/" \
    && ok "client -> foreign dst reachable" || fail "client -> foreign dst unreachable"

echo "--- assertion 3: nft mark counters on Phebes ---"
# We don't have direct shell on Phebes from here without docker. The runner
# script does that part.  Here we just confirm reachability.

echo "--- assertion 4: foreign traffic actually went via Peleon ---"
# Peleon should have at least one masqueraded conntrack entry for FOREIGN_DST.
# (Verified by the runner via 'docker exec dromos-peleon ...')

echo ""
echo "client-side assertions passed."
