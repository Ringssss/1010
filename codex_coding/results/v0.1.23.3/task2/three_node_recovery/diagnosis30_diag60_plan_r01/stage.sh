#!/bin/bash
set -eu
ulimit -c 0
timeout 8s ssh -T -o BatchMode=yes -o ConnectTimeout=5 -o ConnectionAttempts=1 -o StrictHostKeyChecking=yes -o UpdateHostKeys=no -o Hostname=12.11.3.28 node28 'mkdir -p /tmp/fluidckpt_three_node/isolated_r02_n28/project/codex_coding/src/v0.1.23.3/task2/three_node_recovery/diagnosis30'
timeout 8s scp -o BatchMode=yes -o ConnectTimeout=5 -o ConnectionAttempts=1 -o StrictHostKeyChecking=yes -o UpdateHostKeys=no -o Hostname=12.11.3.28 /home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/three_node_recovery/diagnosis30_diag60_plan_r01/source.py node28:/tmp/fluidckpt_three_node/isolated_r02_n28/project/codex_coding/src/v0.1.23.3/task2/three_node_recovery/diagnosis30/init_gate_diag60.py
timeout 8s ssh -T -o BatchMode=yes -o ConnectTimeout=5 -o ConnectionAttempts=1 -o StrictHostKeyChecking=yes -o UpdateHostKeys=no -o Hostname=12.11.3.28 node28 'sha256sum /tmp/fluidckpt_three_node/isolated_r02_n28/project/codex_coding/src/v0.1.23.3/task2/three_node_recovery/diagnosis30/init_gate_diag60.py'
