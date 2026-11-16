#!/usr/bin/env bash
# W6 pipeline: wait for each MaaS archive, unpack it, delete the zip, then run the streaming
# analysis (maas_motivation.py) at the lowest CPU / IO priority (a live experiment shares this host).
#   run_maas_motivation.sh
set -u
D=/home/zhujianian/clusterdata/cluster-trace-v2026-maas/data
U=https://tre-clusterdata.oss-cn-hangzhou.aliyuncs.com/cluster-trace-v2026-maas/data
SRC=/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/src/v0.1.23.3/task2/maas_motivation
OUT=/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/maas_motivation
PY=/home/zhujianian/spack/opt/spack/linux-x86_64_v4/miniconda3-25.5.1-iszegyxvurvtr57qc54q7txr3g2oaxng/envs/sglang/bin/python3
mkdir -p "$OUT"
log() { echo "$(date +'%F %T') $*" | tee -a "$OUT/pipeline.log"; }

fetch() {   # name expected_bytes
  local f=$1 want=$2 have
  until [ -f "$D/$f" ] && [ "$(stat -c %s "$D/$f")" -eq "$want" ]; do
    sleep 60
    have=$(stat -c %s "$D/$f" 2>/dev/null || echo 0)
    # another curl may be writing this file; resume it ourselves only when no curl is fetching it
    # and it has not grown for 5 min (or does not exist)
    if pgrep -f "curl.*-o $f" > /dev/null; then continue; fi
    if [ "$have" -lt "$want" ] && [ $(( $(date +%s) - $(stat -c %Y "$D/$f" 2>/dev/null || echo 0) )) -gt 300 ]; then
      log "resume $f at $have"
      (cd "$D" && curl -fsSL --retry 5 -C - --limit-rate 60M -o "$f" "$U/$f") || log "curl failed for $f"
    fi
  done
  log "complete $f ($want bytes)"
}

unpack() {  # zip
  (cd "$D" && nice -n 19 ionice -c3 unzip -o -q "$1") && rm -f "$D/$1" && log "unpacked $1"
}

run() { log "run $*"; nice -n 19 ionice -c3 "$PY" "$SRC/maas_motivation.py" "$@" >> "$OUT/pipeline.log" 2>&1 || log "FAILED $*"; }

fetch app_metrics.csv.zip 99947 && unpack app_metrics.csv.zip
fetch instance_week_5min.zip 4812189789 && unpack instance_week_5min.zip
run split "$D" "$OUT"
fetch instance_daily.zip 10528173427 && unpack instance_daily.zip
run daily "$D" "$OUT"
run weekly "$D" "$OUT"
run figures "$OUT"
log "pipeline done"
