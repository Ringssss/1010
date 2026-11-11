#!/usr/bin/env bash
# Multi-channel restore with ONE CONTAINER PER GPU.
#
# Why a container per GPU: several RCCL processes inside one container contend with each other and
# their collectives stall (measured 2026-11-10 -- two channels in one container never completed,
# the same two channels on separate containers both hit 44.9 GB/s). One RCCL process per container
# is the working shape, and each container exposes exactly one device so NCCL cannot guess wrong.
#
# usage: run_multi_restore_2.sh <src-node> <dst-node> <park-dir> <tag> <src-cards> <dst-cards>
set -eu
src_node=${1:?}; dst_node=${2:?}; park=${3:?}; tag=${4:?}
src_cards=${5:?}; dst_cards=${6:?}
REPO=/home/zhujianian/zhujianian/fluidckpt_zjn
SCRIPT=$REPO/codex_coding/src/v0.1.23.3/task2/live_graph/gpu/restore_chunks.py
declare -A IP=([node27]=12.11.3.27 [node28]=12.11.3.28 [node29]=12.11.3.29 [node30]=12.11.3.30)

IFS=',' read -ra SRC <<< "$src_cards"
IFS=',' read -ra DST <<< "$dst_cards"
n=${#SRC[@]}
if [ "$n" -ne "${#DST[@]}" ]; then echo "card lists differ in length" >&2; exit 2; fi

launch() {
  local node=$1 role=$2 card=$3 idx=$4
  local master=${IP[$src_node]}:$((43000 + tag * 100 + idx * 10))
  local nch=$nsh
  local ack=$((44000 + tag * 100 + idx * 10))
  ssh -n -o BatchMode=yes "$node" "docker exec -d zjn-fluid-live-g$card bash -lc '
    cd $REPO; export PYTHONPATH=main_lib
    LD_LIBRARY_PATH=/opt/hyhal/lib:\$LD_LIBRARY_PATH
    export NCCL_NET_PLUGIN=librccl-net-shca.so NCCL_SOCKET_IFNAME=ib0 NCCL_DEBUG=WARN
    ulimit -c 0
    python3 $SCRIPT --role $role --master $master --ack-port $ack --peer-ip ${IP[$src_node]} \
      --park-dir $park --gpu 0 --shard-index $idx --shard-count $nch \
      > /root/multi2_${tag}_${idx}_${role}.log 2>&1'"
}

nsh=$n
for i in $(seq 0 $((n - 1))); do launch "$dst_node" dest "${DST[$i]}" "$i"; done
sleep 3
for i in $(seq 0 $((n - 1))); do launch "$src_node" source "${SRC[$i]}" "$i"; done
echo "launched $n container-per-gpu channels: src=$src_node[$src_cards] -> dst=$dst_node[$dst_cards]"
