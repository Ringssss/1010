#!/bin/bash
# Reproduce every node27 result in docs/findings_node27.md, in order. Run inside the container:
#   docker exec zhujianian-fluidckpt bash -lc /home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/src/reproduce_node27.sh
# Total ~2 h. Each measurement spawns fresh workers that only see their own GPUs.
set -e
cd /home/zhujianian/zhujianian/fluidckpt_zjn
source /opt/dtk/env.sh >/dev/null 2>&1
R=benchmarks/results
F="grep --line-buffered -v hylog"
# 1. profile: alpha/beta, single-flow matrices, training mixes, validation set 0
python -m fluidckpt.profiler.run --out $R/profile_node27.json 2>&1 | $F
python -m fluidckpt.profiler.run --only train2 --out $R/profile_node27_train2.json 2>&1 | $F
# 2. fit all variants; the frozen choice is V9_links_hswio_numa_rw_conflict_tied (docs/model_freeze.md)
python -m fluidckpt.model.fit $R/profile_node27.json $R/profile_node27_train2.json --out $R/model_fit_node27_frozen.json 2>&1 | $F
# 3. held-out sets measured after the freeze, scored without refitting
python -m fluidckpt.profiler.run --only validation2,validation3 --out $R/profile_node27_heldout.json 2>&1 | $F
python -m fluidckpt.profiler.run --only validation4 --out $R/profile_node27_policy.json 2>&1 | $F
python -m fluidckpt.profiler.run --only train3 --out $R/profile_node27_train3.json 2>&1 | $F
python -m fluidckpt.model.score_frozen $R/profile_node27.json $R/model_fit_node27_frozen.json \
    $R/profile_node27_heldout.json $R/profile_node27_policy.json --out $R/model_heldout_node27.json 2>&1 | $F
python scripts/purity_split.py
# 4. plans on real hardware: every planner, then the linear-regime LP
M="--profile $R/profile_node27.json --fit $R/model_fit_node27_frozen.json --variant V9_links_hswio_numa_rw_conflict_tied"
python -m fluidckpt.executor.run_plans $M --out $R/plans_node27.json 2>&1 | $F
python -m fluidckpt.executor.run_plans $M --only linear --out $R/plans_node27_linear_v2.json 2>&1 | $F
