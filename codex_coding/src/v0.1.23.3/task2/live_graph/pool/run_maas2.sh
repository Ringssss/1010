#!/usr/bin/env bash
# Four-node, equal-budget comparison: the three systems one after another on a clean pool.
#   run_maas2.sh <out_dir> [extra maas2_run.py args]     (daemons: NODED_ARGS="--arena-gib 48" pool_ctl.sh start)
set -u
out=${1:?out dir}; shift
REPO=/home/zhujianian/zhujianian/fluidckpt_zjn
P=$REPO/codex_coding/src/v0.1.23.3/task2/live_graph/pool
R=$REPO/codex_coding/results/v0.1.23.3/task2/live_graph/pool_r02
mkdir -p "$out"
for s in ${SYSTEMS:-fluidpool janus sglang}; do
  date +"%F %T start $s" >> "$out/progress.log"
  docker exec zjn-fluid-pool-27 bash -lc "cd $REPO && python3 $P/maas2_run.py $s --out $out --manifests $R/manifests $* > $out/${s}_stdout.log 2>&1"
  date +"%F %T done $s rc=$?" >> "$out/progress.log"
done
docker exec zjn-fluid-pool-27 bash -lc "cd $REPO && python3 $P/summarize_maas2.py $out > $out/comparison.md 2>&1"
date +"%F %T all done" >> "$out/progress.log"
