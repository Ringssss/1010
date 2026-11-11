#!/usr/bin/env bash
# Launch bcast_restore.py as a source on one node and a destination on another. Run from any node.
# usage: run_bcast_restore.sh <src-node> <dst-node> <gib> <tag> [--host-staged]
set -eu
src_node=${1:?}; dst_node=${2:?}; gib=${3:-1}; tag=${4:-1}; shift 4 || true
extra="$*"
REPO=/home/zhujianian/zhujianian/fluidckpt_zjn
SCRIPT=$REPO/codex_coding/src/v0.1.23.3/task2/live_graph/gpu/bcast_restore.py
# rank 0 is the source, so the rendezvous point must be the SOURCE node's IB address;
# pointing both ends at a third node leaves the receiver waiting on a store nobody serves.
declare -A IP=([node27]=12.11.3.27 [node28]=12.11.3.28 [node29]=12.11.3.29 [node30]=12.11.3.30)
MASTER=${IP[$src_node]}:$((40400 + tag * 10))
ACK_PORT=$((40500 + tag * 10))
OUT=$REPO/codex_coding/results/v0.1.23.3/task2/live_graph/gpu_r01/bcast_$tag
mkdir -p "$OUT"

launch() {
  local node=$1 role=$2
  ssh -n -o BatchMode=yes "$node" "docker exec -d zjn-fluid-live-r01-${node#node} bash -lc '
    cd $REPO; export PYTHONPATH=main_lib
    LD_LIBRARY_PATH=/opt/dtk/lib:/opt/dtk/lib64:/opt/hyhal/lib:/opt/hyhal/lib64:\$LD_LIBRARY_PATH
    export NCCL_NET_PLUGIN=librccl-net-shca.so NCCL_SOCKET_IFNAME=ib0 GLOO_SOCKET_IFNAME=ib0
    export HSA_FORCE_FINE_GRAIN_PCIE=1 NCCL_DEBUG=WARN
    ulimit -c 0
    python3 $SCRIPT --role $role --master $MASTER --gib $gib \
      --ack-port $ACK_PORT --peer-ip ${IP[$src_node]} $extra \
      > /root/bcast_${tag}_${role}.log 2>&1'"
}

launch "$dst_node" dest        # start the receiver first so the source never waits on a store
sleep 3
launch "$src_node" source
echo "launched src=$src_node dst=$dst_node master=$MASTER tag=$tag extra='$extra'"
