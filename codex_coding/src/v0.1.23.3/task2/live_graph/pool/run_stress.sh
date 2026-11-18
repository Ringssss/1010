#!/usr/bin/env bash
# Stress campaign (2026-11-16): bigger catalog (13 models, ~1.2 TiB), more bursts; fluidpool_v2 vs
# Janus-style vs SGLang-style. Steps (each logged to $LOG):
#   0. wait for fluidpool_v2 of formal_r400_v2 to finish, stop the rest of that queue (ablations
#      postponed), summarize fluidpool_v2 next to the p1b systems (same seed and schedule);
#   1. byte-distinct variants (make_variants.py) on their durable nodes, then their manifests;
#   2. S1 big catalog, default bursts; S2 big catalog, heavy bursts (burst-load 4, amp 60);
#      fresh daemons and a frozen source snapshot per scenario, three systems each.
#   run_stress.sh <campaign_dir>
set -u
C=${1:?campaign dir}
REPO=/home/zhujianian/zhujianian/fluidckpt_zjn
P=$REPO/codex_coding/src/v0.1.23.3/task2/live_graph/pool
R3=$REPO/codex_coding/results/v0.1.23.3/task2/live_graph/pool_r03
MAN=$REPO/codex_coding/results/v0.1.23.3/task2/live_graph/pool_r02/manifests
V2=$R3/formal_r400_v2
mkdir -p "$C"
LOG=$C/campaign.log
say() { echo "$(date +'%F %T') $*" | tee -a "$LOG"; }

# ---- 0. end of fluidpool_v2 (skipped when an earlier invocation already did it)
if ! grep -q "fluidpool_v2 done" "$LOG" 2>/dev/null; then
say "waiting for fluidpool_v2"
until grep -q "done fluidpool_v2" "$V2/progress.log" 2>/dev/null; do sleep 20; done
pkill -f "run_maas3_fro[z]en.sh" ; sleep 1
docker exec zjn-fluid-pool-27 pkill -f "formal_r400_v2/src_snapshot/pool/maas3_ru[n].py"
echo "$(date +'%F %T') stopped after fluidpool_v2 by run_stress.sh: ablations postponed for the stress campaign" >> "$V2/progress.log"
CMP=$R3/formal_r400_cmp_v2_vs_p1b
mkdir -p "$CMP"
for s in fluidpool fluidpool_relay janus sglang; do ln -sfn "$R3/formal_r400_p1b/$s" "$CMP/$s"; done
ln -sfn "$V2/fluidpool_v2" "$CMP/fluidpool_v2"
cp "$R3/formal_r400_p1b/burst_schedule.json" "$CMP/"
docker exec zjn-fluid-pool-27 bash -lc "cd $REPO && python3 $P/summarize_maas3.py $CMP > $CMP/summarize_stdout.log 2>&1"
say "fluidpool_v2 done; comparison in $CMP"
fi

# ---- 1. variants and manifests
declare -A SRC=([qwen36-35b]=/data/node28/shared/zhujianian/Qwen3.6-35B-A3B
                [clover-35b]=/data/node30/shared/models/Clover-35B-A3B-Preview
                [9g-8b]=/data/node28/shared/models/9g_8b_thinking_llama)
# name base durable key
VARS="qwen36-35b-v1:qwen36-35b:node28:17 9g-8b-v1:9g-8b:node28:29 clover-35b-v1:clover-35b:node30:41
qwen36-35b-v2:qwen36-35b:node29:53 9g-8b-v2:9g-8b:node29:67 clover-35b-v2:clover-35b:node28:79
qwen36-35b-v3:qwen36-35b:node30:97"
for node in node28 node29 node30; do
  (
    for v in $VARS; do
      IFS=: read -r name base dn key <<< "$v"
      [ "$dn" = "$node" ] || continue
      dst=/data/$dn/shared/zjn_fluidpool_models/$name
      if [ -f "$MAN/$name.json" ]; then echo "$name manifest exists"; continue; fi
      # /data is read-only inside the pool containers: the variant is written by the host user
      timeout 3000 ssh -n "$dn" "python3 -I $P/make_variants.py ${SRC[$base]} $dst $key" >> "$C/variants_$node.log" 2>&1
      timeout 3000 ssh -n "$dn" "docker exec zjn-fluid-pool-${dn#node} bash -lc 'python3 $P/build_manifests.py $MAN $name=$dst'" >> "$C/variants_$node.log" 2>&1
    done
  ) &
done
wait
for v in $VARS; do
  IFS=: read -r name _ _ _ <<< "$v"
  n=$(python3 -I -c "import json,sys; print(len(json.load(open(sys.argv[1]))['chunks']))" "$MAN/$name.json" 2>/dev/null || echo 0)
  [ "${n:-0}" -gt 0 ] || { say "FAIL: no usable manifest for $name ($n chunks)"; exit 1; }
done
say "variants and manifests ready"

# ---- 2. scenarios
scenario() {   # name, extra args
  local name=$1; shift
  local O=$C/$name
  mkdir -p "$O/daemon_logs"
  NODED_ARGS="--arena-gib 48" bash "$P/pool_ctl.sh" start "$O/daemon_logs" > /dev/null
  timeout 300 bash -c "until [ \$(grep -l '\"serving\"' $O/daemon_logs/noded_node*.log 2>/dev/null | wc -l) -ge 4 ]; do sleep 3; done" \
    || { say "FAIL: daemons for $name"; return 1; }
  say "start $name: $*"
  SYSTEMS="fluidpool_v2 janus sglang" bash "$P/run_maas3_frozen.sh" "$O" "$@" > "$O/driver.log" 2>&1
  say "done $name"
}
scenario S1_big_default --catalog big --requests 300 --calm 15 --seed 1 --warm-arenas 60
scenario S2_big_heavy --catalog big --requests 300 --calm 15 --seed 1 --warm-arenas 60 --burst-load 4 --amp 60
say "campaign done"
