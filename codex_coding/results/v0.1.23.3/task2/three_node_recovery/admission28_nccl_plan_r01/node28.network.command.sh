#!/bin/bash
set -u
ulimit -c 0
timeout --signal=TERM 18s ssh -T -o BatchMode=yes -o ConnectTimeout=5 -o ConnectionAttempts=1 -o StrictHostKeyChecking=yes -o UpdateHostKeys=no -o ServerAliveInterval=3 -o ServerAliveCountMax=1 -o Hostname=12.11.3.28 node28 'python3 - node28' < /home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/src/v0.1.23.3/task2/three_node_recovery/admission28/network_gate_host.py
