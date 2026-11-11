#!/usr/bin/env bash
# Launch one test_mover rank per node and collect both logs. Run on node27.
# usage: run_mover_test.sh <gib> <chunks> <port-tag>
set -eu
gib=${1:-1}
chunks=${2:-4}
num=${3:-1}                      # numeric run tag: also the master/ack port offset
tag=$num
REPO=/home/zhujianian/zhujianian/fluidckpt_zjn
SCRIPT=$REPO/codex_coding/src/v0.1.23.3/task2/live_graph/gpu/test_mover.py
OUT=$REPO/codex_coding/results/v0.1.23.3/task2/live_graph/gpu_r01/mover_$tag
mkdir -p "$OUT"
PEERS='{"0":"12.11.3.27","1":"12.11.3.28"}'
MASTER=12.11.3.27:$((39750 + tag * 10))
ACK=$((39800 + tag * 10))

for spec in "node27 0" "node28 1"; do
  set -- $spec
  node=$1; rank=$2
  ssh -n -o BatchMode=yes "$node" "docker exec -d zjn-fluid-live-r01-${node#node} bash -lc '
    cd $REPO; export PYTHONPATH=main_lib
    LD_LIBRARY_PATH=/opt/rccl-rdma-sharp-plugins/lib:/opt/dtk/lib:/opt/dtk/lib64:/opt/hyhal/lib:/opt/hyhal/lib64:\$LD_LIBRARY_PATH
    export NCCL_NET_PLUGIN=librccl-net-shca.so NCCL_SOCKET_IFNAME=ib0 GLOO_SOCKET_IFNAME=ib0
    export HSA_FORCE_FINE_GRAIN_PCIE=1
    ulimit -c 0
    python3 $SCRIPT --rank $rank --master $MASTER \
      --peers $REPO/codex_coding/results/v0.1.23.3/task2/live_graph/gpu_r01/peers_2.json \
      --ack-base $ACK \
      --gib $gib --chunks $chunks > /root/mover_${tag}_r${rank}.log 2>&1'"
done
echo "launched master=$MASTER ack=$ACK out=$OUT"
