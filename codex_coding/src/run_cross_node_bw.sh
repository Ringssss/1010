#!/bin/bash
# Run scripts/cross_node_bw.py between two nodes, inside their containers. /home is shared (node27
# exports it over IB), so the script itself is identical on both sides.
#
#   scripts/run_cross_node_bw.sh <rank0_host> <rank1_host> <tag> [sizes_MiB]
#   e.g. scripts/run_cross_node_bw.sh node27 node29 node27-node29
#
# torchrun's rendezvous was unreliable across these hosts (a 12 s store delay, then a hang), so the
# group is launched the plain way: explicit RANK/WORLD_SIZE env + the same master address.
set -u
A=$1; B=$2; TAG=$3; SIZES=${4:-}
PORT=${PORT:-29611}
case $A in
  node27) IP=12.11.3.27; CTRA=zhujianian-fluidckpt;;
  *)      IP=12.11.3.$((${A#node})); CTRA=$(ssh "$A" 'docker ps --format "{{.Names}}" | grep -m1 zjn-fluidckpt');;
esac
CTRB=$(ssh "$B" 'docker ps --format "{{.Names}}" | grep -m1 zjn-fluidckpt')
[ -z "$CTRA" ] || [ -z "$CTRB" ] && { echo "missing container: A=$CTRA B=$CTRB"; exit 1; }
ADDR="$IP"
OUT="benchmarks/results/cross_node_${TAG//-/_}.json"
run() { # run <container> <host> <rank>
  local env="MASTER_ADDR=$ADDR MASTER_PORT=$PORT RANK=$3 WORLD_SIZE=2 LOCAL_RANK=0"
  ssh -o BatchMode=yes "$2" "docker exec $1 bash -lc 'cd /home/zhujianian/zhujianian/fluidckpt_zjn && \
    source /opt/dtk/env.sh >/dev/null 2>&1 && $env NCCL_DEBUG=WARN python scripts/cross_node_bw.py \
    --out $OUT --tag $TAG ${SIZES:+--sizes $SIZES} 2>&1 | grep -v hylog | tail -20'"
}
echo "[$TAG] rank0=$A($CTRA) rank1=$B($CTRB) master=$ADDR:$PORT"
run "$CTRB" "$B" 1 > /tmp/cnb_${TAG}_r1.log 2>&1 &
sleep 2
run "$CTRA" "$A" 0 > /tmp/cnb_${TAG}_r0.log 2>&1
wait
echo "=== rank0 ($A) ==="; cat /tmp/cnb_${TAG}_r0.log
echo "=== rank1 ($B) ==="; cat /tmp/cnb_${TAG}_r1.log
