#!/usr/bin/env bash
# Like run_maas3.sh, but the driver runs from a frozen copy of the sources taken when the run
# starts (<out_dir>/src_snapshot), so that editing the working tree during a multi-hour run cannot
# give the three systems different code. The node daemons keep the code they were started with:
# start them right before this script and do not restart them until it ends.
#   run_maas3_frozen.sh <out_dir> [extra maas3_run.py args]
set -u
out=${1:?out dir}; shift
REPO=/home/zhujianian/zhujianian/fluidckpt_zjn
P=$REPO/codex_coding/src/v0.1.23.3/task2/live_graph/pool
R=$REPO/codex_coding/results/v0.1.23.3/task2/live_graph/pool_r02
S=$out/src_snapshot
mkdir -p "$S/main_lib" "$S/pool"
if [ ! -f "$S/DONE" ]; then
  cp -r "$REPO/main_lib/fluidckpt" "$S/main_lib/"
  find "$S/main_lib" -name __pycache__ -prune -exec rm -rf {} +
  cp "$P"/maas3_run.py "$P"/summarize_maas3.py "$P"/tenantd.py "$S/pool/"
  (cd "$REPO" && git rev-parse HEAD && git status --short -- main_lib codex_coding/src) > "$S/git_state.txt" 2>&1
  (cd "$S" && find . -type f ! -name sha256.txt -exec sha256sum {} + | sort -k2) > "$S/sha256.txt"
  date +"%F %T" > "$S/DONE"
fi
for s in ${SYSTEMS:-fluidpool janus sglang}; do
  date +"%F %T start $s" >> "$out/progress.log"
  docker exec -e PYTHONPATH=$S/main_lib zjn-fluid-pool-27 bash -lc "cd $REPO && python3 $S/pool/maas3_run.py $s --out $out --manifests $R/manifests $* > $out/${s}_stdout.log 2>&1"
  date +"%F %T done $s rc=$?" >> "$out/progress.log"
done
docker exec -e PYTHONPATH=$S/main_lib zjn-fluid-pool-27 bash -lc "cd $REPO && python3 $S/pool/summarize_maas3.py $out > $out/summarize_stdout.log 2>&1"
date +"%F %T all done" >> "$out/progress.log"
