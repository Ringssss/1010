#!/bin/bash
# Generic two-node launcher: runs a python script on two hosts inside their FluidCKPT containers.
# /home is shared, so the script is identical on both sides. One process per node by default;
# set NPROC=k to start k ranks per node (world = 2k).
#
#   scripts/run_on_pair.sh <script.py> <rank0_host> <rank1_host> [extra env...] [-- args...]
#   EXTRA=1 scripts/run_on_pair.sh scripts/foo.py node27 node29 NCCL_DEBUG=INFO
#   NPROC=8 PORT=29800 scripts/run_on_pair.sh scripts/multi_rank_bw.py node27 node29 \
#       NCCL_NET_PLUGIN=librccl-net-shca.so -- --out out.json
#
# torchrun's rendezvous was unreliable between these hosts, so ranks are started with explicit
# RANK/WORLD_SIZE/LOCAL_RANK env and the same MASTER_ADDR. Logs land in /tmp/pair_<host>_r<k>.log.
set -u
SCRIPT=$1; A=$2; B=$3; shift 3
ENVS=""
ARGS=""
while [ $# -gt 0 ]; do
  case $1 in --) shift; ARGS="$*"; break;; *) ENVS="$ENVS $1"; shift;; esac
done
PORT=${PORT:-29701}
NPROC=${NPROC:-1}
WORLD=$((2 * NPROC))
ip_of() { case $1 in node27) echo 12.11.3.27;; node28) echo 12.11.3.28;; node29) echo 12.11.3.29;; node30) echo 12.11.3.30;; esac; }
ctr_of() {
  if [ "$1" = node27 ]; then echo zhujianian-fluidckpt
  else ssh -o BatchMode=yes "$1" 'docker ps --format "{{.Names}}" | grep -m1 zjn-fluidckpt'; fi
}
ADDR=$(ip_of "$A"); CTRA=$(ctr_of "$A"); CTRB=$(ctr_of "$B")
[ -z "$CTRA" ] || [ -z "$CTRB" ] && { echo "missing container: $A=$CTRA $B=$CTRB"; exit 1; }
TAG="${A}-${B}"

# launch_all <ctr> <host> <rank_base> <tag>
launch_all() {
  local ctr=$1 host=$2 base=$3 tag=$4
  for ((l = 0; l < NPROC; l++)); do
    local r=$((base + l))
    ssh -o BatchMode=yes "$host" "docker exec $ctr bash -lc 'cd /home/zhujianian/zhujianian/fluidckpt_zjn && \
        source /opt/dtk/env.sh >/dev/null 2>&1 && \
        MASTER_ADDR=$ADDR MASTER_PORT=$PORT RANK=$r WORLD_SIZE=$WORLD LOCAL_RANK=$l $ENVS \
        python $SCRIPT $ARGS 2>&1 | grep -v hylog'" > "/tmp/pair_${tag}_r${r}.log" 2>&1 &
  done
}
echo "[$TAG] $SCRIPT  proc/node=$NPROC world=$WORLD  node0=$A($CTRA) node1=$B($CTRB)  master=$ADDR:$PORT"
echo "[$TAG] env='$ENVS' args='$ARGS'"
launch_all "$CTRB" "$B" "$NPROC" "$TAG"
sleep 3
launch_all "$CTRA" "$A" 0 "$TAG"
wait
for ((r = 0; r < WORLD; r++)); do
  f="/tmp/pair_${TAG}_r${r}.log"
  if [ -s "$f" ]; then echo "=== rank$r ==="; cat "$f"; fi
done
