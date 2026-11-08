#!/bin/bash
# Round 2 (fresh runs after the round-1 plan execution): regime-restricted LP, the fan-out clone
# scenario with every planner, and a policy-regime held-out profile scored with the frozen model.
set -e
cd /home/zhujianian/zhujianian/fluidckpt_zjn
source /opt/dtk/env.sh >/dev/null 2>&1
M="--profile benchmarks/results/profile_node27.json --fit benchmarks/results/model_fit_node27_frozen.json --variant V9_links_hswio_numa_rw_conflict_tied"
python -m fluidckpt.executor.run_plans $M --only linear --out benchmarks/results/plans_node27_linear.json 2>&1 | grep --line-buffered -v hylog
python -m fluidckpt.executor.run_plans $M --scenarios 2xtp2_clone_from_one_running --out benchmarks/results/plans_node27_clone.json 2>&1 | grep --line-buffered -v hylog
python -m fluidckpt.profiler.run --only validation4 --out benchmarks/results/profile_node27_policy.json 2>&1 | grep --line-buffered -v hylog
python -m fluidckpt.model.score_frozen benchmarks/results/profile_node27.json benchmarks/results/model_fit_node27_frozen.json \
    benchmarks/results/profile_node27_heldout.json benchmarks/results/profile_node27_policy.json \
    --out benchmarks/results/model_heldout_node27.json 2>&1 | grep --line-buffered -v hylog
