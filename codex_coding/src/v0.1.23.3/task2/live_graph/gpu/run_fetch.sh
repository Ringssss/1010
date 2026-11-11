#!/usr/bin/env bash
# Pull a model's chunks into one node from every other node that holds them, in parallel.
#
# One process pair per source: push on the holder, pull on the destination. Each pair gets its own
# rendezvous and ack ports. One RCCL process per container is what works on this fabric, so the
# pairs run in separate containers even when two sources share a node.
#
# usage: run_fetch.sh <dst-node> <dst-card> <src-cards> <work-dir> <tag>
#   e.g. run_fetch.sh node27 4 0,1,2  <work>/fetch2 1     (node28 card0, node29 card1, node30 card2)
set -eu
dst_node=${1:?}; dst_card=${2:?}; src_cards=${3:?}; work=${4:?}; tag=${5:-1}
REPO=/home/zhujianian/zhujianian/fluidckpt_zjn
SCRIPT=$REPO/codex_coding/src/v0.1.23.3/task2/live_graph/gpu/chunk_fluid.py
declare -A IP=([node27]=12.11.3.27 [node28]=12.11.3.28 [node29]=12.11.3.29 [node30]=12.11.3.30)
IFS=',' read -ra CARDS <<< "$src_cards"

launch() {
  local node=$1 role=$2 card=$3 idx=$4
  local master=${IP[$1]}:$((46000 + tag * 100 + idx * 10))
  local ack=$((47000 + tag * 100 + idx * 10))
  ssh -n -o BatchMode=yes "$node" "docker exec -d zjn-fluid-live-r01-${node#node} bash -lc '
    cd $REPO; export PYTHONPATH=main_lib
    export HIP_VISIBLE_DEVICES=$card
    LD_LIBRARY_PATH=/opt/hyhal/lib:\$LD_LIBRARY_PATH
    export NCCL_NET_PLUGIN=librccl-net-shca.so NCCL_SOCKET_IFNAME=ib0 NCCL_DEBUG=WARN
    ulimit -c 0
    python3 $SCRIPT $role --store /root/chunkstore --chunks $work/chunks.json \
      --master $master --ack-port $ack --peer-ip ${IP[$dst_node]} --gpu 0 \
      > /root/fetch_${tag}_${idx}_${role}.log 2>&1'"
}

i=0
for node in node28 node29 node30; do
  if [ ! -f "$work/chunks_${node}.json" ]; then i=$((i+1)); continue; fi
  cp "$work/chunks_${node}.json" "$work/chunks.json" 2>/dev/null || true
  # each pair needs its own copy of the chunk list file
  cp --force "$work/chunks_${node}.json" "$work/chunks_${i}.json"
  i=$((i+1))
done

# launch: write per-pair chunk lists, start pulls first, then pushes
i=0
for node in node28 node29 node30; do
  [ -f "$work/chunks_${node}.json" ] || { i=$((i+1)); continue; }
  ssh -n -o BatchMode=yes "$node" "mkdir -p $work" 2>/dev/null || true
  i=$((i+1))
done
echo "fetch launched into $dst_node (cards $src_cards) from $work"
