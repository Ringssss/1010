#!/bin/bash
set -u
ulimit -c 0
timeout --signal=TERM 15s ssh -T -o BatchMode=yes -o ConnectTimeout=5 -o ConnectionAttempts=1 -o StrictHostKeyChecking=yes -o UpdateHostKeys=no -o ServerAliveInterval=3 -o ServerAliveCountMax=1 -o Hostname=12.11.3.28 node28 'docker exec -i -e PYTHONDONTWRITEBYTECODE=1 e693e946828a37dbf4daa3bc25855b1c28f88bd02cdd93ce0d2e95f33e37d3ba python3 -' < /home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/three_node_recovery/admission28_nccl_deploy_r01/node28.stage.payload.py
