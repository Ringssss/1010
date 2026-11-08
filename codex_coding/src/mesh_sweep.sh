"""Full-mesh cross-node bandwidth sweep: every ordered pair of nodes, one process each side.

Writes one JSON per pair via scripts/cross_node_bw.py. Run the pairs one at a time (each pair
needs the whole two-host rendezvous).

  scripts/mesh_sweep.sh [sizes_MiB]     # e.g. scripts/mesh_sweep.sh 256,1024
"""
set -u
SIZES=${1:-256,1024}
NODES=(node27 node28 node29)
cd /home/zhujianian/zhujianian/fluidckpt_zjn
PORT=${PORT:-29801}
for ((i = 0; i < ${#NODES[@]}; i++)); do
  for ((j = i + 1; j < ${#NODES[@]}; j++)); do
    A=${NODES[$i]}; B=${NODES[$j]}
    PORT=$((PORT + 10))
    echo "################ $A <-> $B ################"
    PORT=$PORT bash scripts/run_on_pair.sh scripts/cross_node_bw.py "$A" "$B" \
      NCCL_NET_PLUGIN=librccl-net-shca.so NCCL_DEBUG=WARN \
      -- --out "benchmarks/results/mesh_${A#node}_${B#node}.json" --tag "${A#node}-${B#node}" \
         --sizes "$SIZES" 2>&1 | grep -E "MiB|wrote|Error|error" | head -10
  done
done
