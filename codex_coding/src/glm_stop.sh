#!/bin/bash
# Kill every sglang / fluid launcher process in the FluidCKPT containers on the given nodes.
for h in "$@"; do
  ssh -o BatchMode=yes $h 'C=$(docker ps --format "{{.Names}}" | grep -m1 -E "zjn-fluidckpt|zhujianian-fluidckpt"); docker exec $C bash -c "pkill -9 -f sglang_fluid_launch; pkill -9 -f sglang.launch_server; pkill -9 -f sglang::; sleep 1; pgrep -fa sglang | head -3"'
done
