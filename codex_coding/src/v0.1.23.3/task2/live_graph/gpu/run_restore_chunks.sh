#!/usr/bin/env bash
# Restore a real model from a parked copy on <src-node> into <dst-node>. Run from any node.
# usage: run_restore_chunks.sh <src-node> <dst-node> <park-dir> <tag>
set -eu
src_node=${1:?}; dst_node=${2:?}; park=${3:?}; tag=${4:-1}
REPO=/home/zhujianian/zhujianian/fluidckpt_zjn
SCRIPT=$REPO/codex_coding/src/v0.1.23.3/task2/live_graph/gpu/restore_chunks.py
declare -A IP=([node27]=12.11.3.27 [node28]=12.11.3.28 [node29]=12.11.3.29 [node30]=12.11.3.30)
MASTER=${IP[$src_node]}:$((40700 + tag * 10))
ACK=$((40800 + tag * 10))
OUT=$REPO/codex_coding/results/v0.1.23.3/task2/live_graph/gpu_r01/restore_$tag
mkdir -p "$OUT"

launch() {
  local node=$1 role=$2
  ssh -n -o BatchMode=yes "$node" "docker exec -d zjn-fluid-live-r01-${node#node} bash -lc '
    cd $REPO; export PYTHONPATH=main_lib
    export HIP_VISIBLE_DEVICES=${HIP_VIS:-0,1,2,3}
    LD_LIBRARY_PATH=/opt/hyhal/lib:\$LD_LIBRARY_PATH
    export NCCL_NET_PLUGIN=librccl-net-shca.so NCCL_SOCKET_IFNAME=ib0 NCCL_DEBUG=WARN
    ulimit -c 0
    python3 $SCRIPT --role $role --master $MASTER --ack-port $ACK --peer-ip ${IP[$src_node]} \
      --park-dir $park --plan-dir $OUT --chunk-mib ${CHUNK_MIB:-256} ${PINNED:+--pinned} \
      > /root/restore_${tag}_${role}.log 2>&1'"
}

launch "$dst_node" dest
sleep 3
launch "$src_node" source
echo "launched src=$src_node dst=$dst_node master=$MASTER tag=$tag out=$OUT"
