#!/bin/bash
# Launch GLM-5.3 tp16 across two nodes inside the FluidCKPT containers.
#   scripts/glm_serve.sh <tag> <node0> <node1> [extra sglang args...]
# env: FLUIDCKPT_MODE=capture|restore|"" (vanilla)   PORT (http, default 31753)
set -u
TAG=$1; A=$2; B=$3; shift 3
EXTRA="$*"
MODE=${FLUIDCKPT_MODE:-}
PORT=${PORT:-31753}
DPORT=${DPORT:-26300}
ip_of() { case $1 in node27) echo 12.11.3.27;; node28) echo 12.11.3.28;; node29) echo 12.11.3.29;; esac; }
ctr_of() { ssh -o BatchMode=yes "$1" 'docker ps --format "{{.Names}}" | grep -m1 -E "zjn-fluidckpt|zhujianian-fluidckpt"'; }
ADDR=$(ip_of $A)
LOGD=/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/logs
ENTRY=python3; [ -n "$MODE" ] && ENTRY="python3 scripts/sglang_fluid_launch.py" || ENTRY="python3 -m sglang.launch_server"
start() {
  local host=$1 rank=$2 ctr; ctr=$(ctr_of $host)
  ssh -o BatchMode=yes $host "docker exec -d $ctr bash -lc 'ulimit -c 0; cd /home/zhujianian/zhujianian/fluidckpt_zjn && source /opt/dtk/env.sh >/dev/null 2>&1 && unset NCCL_TOPO_FILE; \
    export NCCL_NET_PLUGIN=librccl-net-shca.so NCCL_SOCKET_IFNAME=ib0 GLOO_SOCKET_IFNAME=ib0 NCCL_DEBUG=WARN HSA_FORCE_FINE_GRAIN_PCIE=1 \
      HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 FLUIDCKPT_MODE=$MODE FLUIDCKPT_TAG=$TAG FLUIDCKPT_HANDOFF=/dev/shm/fluidckpt \
      FLUIDCKPT_MANIFEST=${FLUIDCKPT_MANIFEST:-} FLUIDCKPT_CAPTURE_WAIT=${FLUIDCKPT_CAPTURE_WAIT:-}; \
    date +%s.%N > $LOGD/${TAG}_n${rank}.t0; \
    exec $ENTRY --model-path /data/node28/shared/zhujianian/GLM-5.3 --tp-size 16 --nnodes 2 --node-rank $rank \
      --dist-init-addr $ADDR:$DPORT --host $ADDR --port $PORT --trust-remote-code \
      --context-length 16384 --max-total-tokens 32768 --max-running-requests 16 --chunked-prefill-size 4096 \
      --mem-fraction-static 0.85 --disable-cuda-graph --disable-custom-all-reduce --moe-runner-backend triton \
      --kv-cache-dtype bf16 --watchdog-timeout 3600 $EXTRA > $LOGD/${TAG}_n${rank}.log 2>&1'"
}
start $B 1
start $A 0
echo "launched $TAG on $A(0) $B(1) http=$ADDR:$PORT mode='$MODE'"
