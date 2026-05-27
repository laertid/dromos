#!/bin/sh
# Client: only one network (client_net), default route to Phebes.
set -eu
PHEBES_IP=172.30.10.4
ip route replace default via "$PHEBES_IP"
echo "[client] default via $PHEBES_IP"
