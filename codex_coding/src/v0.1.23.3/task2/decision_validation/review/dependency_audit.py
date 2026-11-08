"""Audit the two-worker service dependency gap using explicit synthetic costs."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.cluster.path_catalog import topology_id
from fluidckpt.cluster.planning import restore_plan
from fluidckpt.cluster.service_cost import estimate_execution, new_state


def audit():
    workers = [dict(worker=i, node=f"node{i}", gpu=0, numa=0, session="synthetic",
                    epoch=f"synthetic{i}", capabilities=["local_hbm", "network"])
               for i in range(2)]
    profile = dict(rates=dict(local_hbm=10., network=1.), gpu_ingress_GBps=100.,
                   gpu_egress_GBps=100., dram_GBps=100., network_node_GBps=100.,
                   serial_target_execution=True)
    targets = [dict(key=f"target{i}", worker=i, identity="synthetic", tp=i, off=0,
                    n=16 << 20, tier="hbm") for i in range(2)]
    rows = {}
    for name, locations in dict(A=[0, 1], B=[0, 0], C=[1, 1], D=[1, 0]).items():
        replicas = [dict(id=f"{name}{i}", identity="synthetic", tp=i, off=0,
                         n=16 << 20, worker=w, node=f"node{w}", gpu=0, numa=0,
                         tier="hbm", state="VALID", generation=1, epoch=f"synthetic{w}")
                    for i, w in enumerate(locations)]
        plan = restore_plan(replicas, targets, workers, profile, "nearest")
        model = dict(schema_version=2, topology_id=topology_id(workers), executor="serial",
                     dispatch_s=0., request_s=0., paths={
                         op["path"]: dict(warm_GBps=10. if op["kind"] == "local_hbm" else 1.,
                                          operation_s=0., first_pair_s=0.) for op in plan["ops"]},
                     scope="synthetic cost identity; not a measured hardware prediction")
        dependency = estimate_execution(plan, workers, model, new_state(workers))
        rows[name] = dict(resource_target_bound_s=plan["estimated_transfer_s"],
                          worker_dependency_s=dependency["estimated_worker_s"],
                          timeline=dependency["timeline"],
                          ops=[{k: op[k] for k in ("op_id", "src_w", "dst_w", "kind", "n")}
                               for op in plan["ops"]])
    checks = dict(A_bound_equals_dependency=rows["A"]["resource_target_bound_s"] == rows["A"]["worker_dependency_s"],
                  B_bound_misses_local_before_network=rows["B"]["resource_target_bound_s"] < rows["B"]["worker_dependency_s"],
                  C_bound_misses_network_before_local=rows["C"]["resource_target_bound_s"] < rows["C"]["worker_dependency_s"],
                  D_dependency_is_twice_bound=abs(rows["D"]["worker_dependency_s"] - 2*rows["D"]["resource_target_bound_s"]) < 1e-12)
    sources = ["main_lib/fluidckpt/cluster/planning.py", "main_lib/fluidckpt/cluster/service_cost.py",
               "main_lib/fluidckpt/cluster/worker.py", "main_lib/fluidckpt/cluster/multi_joint.py",
               "main_lib/fluidckpt/benchmarks/frozen_layout.py"]
    return dict(schema_version="decision_dependency_audit.v1", checks=checks,
                passed=all(checks.values()), layouts=rows, source_sha256={
                    name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sources},
                measured=False, GPU_or_SSH_mutations=False,
                conclusion="Two-worker serial pair operations have dependency cost absent from resource/target bounds. This countermodel verifies scope, not hardware performance.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(dict(passed=result["passed"], checks=result["checks"])))
