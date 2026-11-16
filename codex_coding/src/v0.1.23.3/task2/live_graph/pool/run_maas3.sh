#!/usr/bin/env bash
# Random-burst comparison: the three systems one after another on a clean pool, same burst schedule.
#   run_maas3.sh <out_dir> [extra maas3_run.py args]     (daemons: NODED_ARGS="--arena-gib 48" pool_ctl.sh start)
# The schedule is written once to <out_dir>/burst_schedule.json; a system started into an out dir
# that holds a different schedule refuses to run. Logs stay in <out_dir> (never /tmp or /root).
set -u
out=${1:?out dir}; shift
REPO=/home/zhujianian/zhujianian/fluidckpt_zjn
P=$REPO/codex_coding/src/v0.1.23.3/task2/live_graph/pool
R=$REPO/codex_coding/results/v0.1.23.3/task2/live_graph/pool_r02
mkdir -p "$out"
for s in ${SYSTEMS:-fluidpool janus sglang}; do
  date +"%F %T start $s" >> "$out/progress.log"
  docker exec zjn-fluid-pool-27 bash -lc "cd $REPO && python3 $P/maas3_run.py $s --out $out --manifests $R/manifests $* > $out/${s}_stdout.log 2>&1"
  date +"%F %T done $s rc=$?" >> "$out/progress.log"
done
docker exec zjn-fluid-pool-27 bash -lc "cd $REPO && python3 $P/summarize_maas3.py $out > $out/summarize_stdout.log 2>&1"
date +"%F %T all done" >> "$out/progress.log"
