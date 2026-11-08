#!/bin/bash
set -eu
ulimit -c 0
timeout 8s ssh -T -o BatchMode=yes -o ConnectTimeout=5 -o ConnectionAttempts=1 -o StrictHostKeyChecking=yes -o UpdateHostKeys=no -o Hostname=12.11.3.30 node30 'mkdir /tmp/fluidckpt_minimal_calibration_r01_bootstrap'
timeout 12s scp -o BatchMode=yes -o ConnectTimeout=5 -o ConnectionAttempts=1 -o StrictHostKeyChecking=yes -o UpdateHostKeys=no -o Hostname=12.11.3.30 codex_coding/results/v0.1.23.3/task2/minimal_calibration/measure_setup_plan_r01/measurement_host.source.py codex_coding/results/v0.1.23.3/task2/minimal_calibration/measure_setup_plan_r01/node30.spec.json node30:/tmp/fluidckpt_minimal_calibration_r01_bootstrap/
timeout 35s ssh -T -o BatchMode=yes -o ConnectTimeout=5 -o ConnectionAttempts=1 -o StrictHostKeyChecking=yes -o UpdateHostKeys=no -o Hostname=12.11.3.30 node30 'python3 /tmp/fluidckpt_minimal_calibration_r01_bootstrap/measurement_host.source.py setup /tmp/fluidckpt_minimal_calibration_r01_bootstrap/node30.spec.json'
