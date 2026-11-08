#!/bin/bash
set -u
ulimit -c 0
timeout 10s ssh -T -o BatchMode=yes -o ConnectTimeout=5 -o ConnectionAttempts=1 -o StrictHostKeyChecking=yes -o UpdateHostKeys=no -o Hostname=12.11.3.28 node28 'docker exec e693e946828a37dbf4daa3bc25855b1c28f88bd02cdd93ce0d2e95f33e37d3ba tail -c 65536 /var/log/hylog/root_python3.10_20261009_143318.930369_43.log'
