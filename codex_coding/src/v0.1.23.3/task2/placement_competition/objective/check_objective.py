"""Small CPU probes of existing worker scheduling and fixed symbolic bounds."""
import argparse
from fractions import Fraction
import hashlib
import itertools
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.cluster.path_catalog import topology_id
from fluidckpt.cluster.service_cost import estimate_execution, new_state


def probe():
    workers = [dict(worker=i, node=f"symbolic_{i}", gpu=0, numa=0,
                    session="objective_cpu_only", epoch=1) for i in range(6)]
    model = dict(schema_version=1, topology_id=topology_id(workers), executor="serial",
                 chunk_bytes=32 << 20, dispatch_s=0.,
                 scope="SYMBOLIC isolated service and worker dependencies; no shared NIC dynamics",
                 paths={"remote": dict(warm_GBps=1., operation_s=0., first_pair_s=0.),
                        "local": dict(warm_GBps=1., operation_s=0., first_pair_s=0.)})

    def op(name, src, dst, path="remote"):
        return dict(op_id=name, src_w=src, dst_w=dst, n=1_000_000_000, path=path)

    def score(ops):
        plan = dict(ops=ops, executor="serial", chunk_bytes=32 << 20,
                    estimated_transfer_s=0.)
        return estimate_execution(plan, workers, model, new_state(workers))

    two_helpers = score([op("r0", 4, 0), op("r1", 5, 1),
                         op("r2", 4, 2), op("r3", 5, 3)])
    reused_roles = score([op("local0", 0, 0, "local"),
                          op("local1", 1, 1, "local"),
                          op("pool0_to_r2", 0, 2), op("pool1_to_r3", 1, 3)])
    local_first = score([op("local0", 0, 0, "local"),
                         op("local1", 1, 1, "local"),
                         op("remote0", 4, 0), op("remote1", 5, 1)])
    # Explicit capacity-feasible witness, not a scheduler or a new solver.
    shared_witness = [dict(op_id="local0", start=0, end=1, workers=[0], resources=[]),
                      dict(op_id="local1", start=0, end=1, workers=[1], resources=[]),
                      dict(op_id="remote0", start=1, end=2, workers=[4, 0], resources=["U"]),
                      dict(op_id="remote1", start=2, end=3, workers=[5, 1], resources=["U"])]
    for left, right in itertools.combinations(shared_witness, 2):
        overlap = max(left["start"], right["start"]) < min(left["end"], right["end"])
        if overlap:
            assert not set(left["workers"]) & set(right["workers"])
            assert not set(left["resources"]) & set(right["resources"])
    # Under the fixed local-first queues all network work is released at t=1.
    # Two bytes-units must cross unit-capacity U afterwards: T >= 1 + 2.
    pair_checks = 0
    for a, b, c, d in itertools.product(range(1, 5), repeat=4):
        first = max(Fraction(1, a), Fraction(1, d))
        crossed = max(Fraction(1, b), Fraction(1, c))
        assert (crossed < first) == (min(b, c) > min(a, d))
        pair_checks += 1
    checks = {
        "two_helper_four_target_dag_is_two_not_independent_one": two_helpers["estimated_execute_s"] == 2.,
        "each_helper_has_two_serial_operations": [row["start_s"] for row in two_helpers["timeline"]] == [0., 0., 1., 1.],
        "reused_pool_target_roles_have_cross_role_predecessors": reused_roles["estimated_execute_s"] == 2.,
        "same_worker_remote_waits_for_local_even_different_target": reused_roles["timeline"][2]["start_s"] == 1.,
        "shared_nic_not_in_original_service_dag": local_first["estimated_execute_s"] == 2.,
        "max_resource_bound_and_dag_can_be_strictly_unattainable": max(2., local_first["estimated_execute_s"]) < 3.,
        "explicit_fixed_queue_shared_witness_matches_release_cut_bound": max(x["end"] for x in shared_witness) == 1 + 2,
        "all_256_assignment_exchange_conditions_exact": pair_checks == 256,
    }
    refs = [Path(__file__).relative_to(ROOT).as_posix(),
            "main_lib/fluidckpt/cluster/service_cost.py",
            "main_lib/fluidckpt/cluster/planning.py",
            "main_lib/fluidckpt/cluster/multi_joint.py",
            "main_lib/fluidckpt/cluster/managed.py",
            "main_lib/fluidckpt/cluster/worker.py",
            "main_lib/fluidckpt/benchmarks/frozen_layout.py",
            "main_lib/fluidckpt/benchmarks/topology_cases.py"]
    return dict(checks=checks, two_helpers=two_helpers, reused_roles=reused_roles,
                local_first_worker_dag=local_first,
                fixed_local_first_shared_resource=dict(cut_load_bound=2, target_serial_bound=2,
                    worker_dag_score=2, fixed_queue_release_cut_bound=3, feasible_witness=shared_witness,
                    scope="fixed zero-setup unit-service symbolic queues, exclusive U; exact for this explicit queue only"),
                exchange_condition=dict(first="s/min(a,d)", crossed="s/min(b,c)",
                    strict_improvement="min(b,c) > min(a,d)", nearest_first="a > b, or declared tie",
                    assumptions="equal size, two one-slot pools, independent source-target pairs, direct-only complete coverage, no fallback or setup, adequate ingress"),
                source_refs=[dict(path=p, sha256=hashlib.sha256((ROOT / p).read_bytes()).hexdigest()) for p in refs],
                qualification="CPU_symbolic_objective_scope_only",
                new_GPU_samples=0, SSH_actions=0, product_mutations=0,
                actual_T_ready_measured=False, shared_contention_calibrated=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    result = probe()
    (args.out / "checks.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if not all(result["checks"].values()):
        raise SystemExit("objective probe failed")
    print(json.dumps(dict(passed=len(result["checks"]), out=str(args.out))))


if __name__ == "__main__":
    main()
