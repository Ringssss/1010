#!/bin/bash
# Round 3: planner restricted to the linear regime (single-node root ports, no NUMA fan-out),
# re-executed on every scenario with the frozen V9 model; full planner set on the 8-GPU local scenario.
set -e
cd /home/zhujianian/zhujianian/fluidckpt_zjn
source /opt/dtk/env.sh >/dev/null 2>&1
R=benchmarks/results
M="--profile $R/profile_node27.json --fit $R/model_fit_node27_frozen.json --variant V9_links_hswio_numa_rw_conflict_tied"
python -m fluidckpt.executor.run_plans $M --only linear --out $R/plans_node27_linear_v2.json 2>&1 | grep --line-buffered -v hylog
python -m fluidckpt.executor.run_plans $M --scenarios 4xtp2_numa_local_dram --out $R/plans_node27_local8.json 2>&1 | grep --line-buffered -v hylog
