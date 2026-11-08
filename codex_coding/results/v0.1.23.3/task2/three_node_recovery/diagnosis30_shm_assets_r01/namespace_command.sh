#!/bin/bash
set -u
ulimit -c 0
timeout --signal=TERM 12s ssh -T -o BatchMode=yes -o ConnectTimeout=5 -o ConnectionAttempts=1 -o StrictHostKeyChecking=yes -o UpdateHostKeys=no -o ServerAliveInterval=3 -o ServerAliveCountMax=1 -o Hostname=12.11.3.30 node30 'docker exec -e PYTHONDONTWRITEBYTECODE=1 7d3143da42c4c16a9f48e61189035e48a2dc3d9f177cd6207cb9b7f447120695 python3 /dev/shm/fluidckpt_diag30_r01/project/codex_coding/src/v0.1.23.3/task2/three_node_recovery/probe/container_namespace.py --host /dev/shm/fluidckpt_diag30_r01/project/codex_coding/results/v0.1.23.3/task2/three_node_recovery/inventory_frozen_isolated_r01/node30.json --out /dev/shm/fluidckpt_diag30_r01/project/codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_namespace_node30_nccl_r01'
