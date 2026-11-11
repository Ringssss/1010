#!/usr/bin/env bash
# Restore one model over N independent pair channels, one GPU per channel, in parallel.
#
# Each channel is its own process pair (source on <src-node>, destination on <dst-node>) with its
# own rendezvous and ack ports. That shape is deliberate: a long-lived multi-rank mesh deadlocks on
# this RCCL, while a two-process pair at 45 GB/s is reproducible.
#
# Every process is given exactly ONE visible device. With several visible, NCCL guesses the device
# from the global rank and picks the wrong card, which stalls the collective forever (measured);
# with one device visible there is nothing to guess.
#
# usage: run_multi_restore.sh <src-node> <dst-node> <park-dir> <tag> <src-cards> <dst-cards>
#   e.g. run_multi_restore.sh node27 node28 <dir> 1 4,5,6,7 0,1,2,3
set -eu
src_node=${1:?}; dst_node=${2:?}; park=${3:?}; tag=${4:?}
src_cards=${5:?}; dst_cards=${6:?}
REPO=/home/zhujianian/zhujianian/fluidckpt_zjn
SCRIPT=$REPO/codex_coding/src/v0.1.23.3/task2/live_graph/gpu/restore_chunks.py
declare -A IP=([node27]=12.11.3.27 [node28]=12.11.3.28 [node29]=12.11.3.29 [node30]=12.11.3.30)
OUT=$REPO/codex_coding/results/v0.1.23.3/task2/live_graph/gpu_r01/multi_$tag
mkdir -p "$OUT"

IFS=',' read -ra SRC <<< "$src_cards"
IFS=',' read -ra DST <<< "$dst_cards"
n=${#SRC[@]}
if [ "$n" -ne "${#DST[@]}" ]; then echo "card lists differ in length" >&2; exit 2; fi

nsh=$n

launch() {
  local node=$1 role=$2 card=$3 idx=$4
  local master=${IP[$src_node]}:$((41000 + tag * 100 + idx * 10))
  local ack=$((42000 + tag * 100 + idx * 10))
  local nch=$nsh
  ssh -n -o BatchMode=yes "$node" "docker exec -d zjn-fluid-live-r01-${node#node} bash -lc '
    cd $REPO; export PYTHONPATH=main_lib
    export HIP_VISIBLE_DEVICES=$card
    LD_LIBRARY_PATH=/opt/hyhal/lib:\$LD_LIBRARY_PATH
    export NCCL_NET_PLUGIN=librccl-net-shca.so NCCL_SOCKET_IFNAME=ib0 NCCL_DEBUG=WARN
    ulimit -c 0
    python3 $SCRIPT --role $role --master $master --ack-port $ack --peer-ip ${IP[$src_node]} \
      --park-dir $park --plan-dir $OUT --chunk-mib ${CHUNK_MIB:-256} --gpu 0 --shard-index $idx --shard-count $nch \
      > /root/restore_${tag}_${idx}_${role}.log 2>&1'"
}

for i in $(seq 0 $((n - 1))); do
  launch "$dst_node" dest "${DST[$i]}" "$i"
done
sleep 3
for i in $(seq 0 $((n - 1))); do
  launch "$src_node" source "${SRC[$i]}" "$i"
done
echo "launched $n channels: src=$src_node[$src_cards] -> dst=$dst_node[$dst_cards] park=$park"
