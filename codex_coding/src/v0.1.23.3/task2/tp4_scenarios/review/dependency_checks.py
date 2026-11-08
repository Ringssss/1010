"""Independent CPU probes of TP4 worker dependencies and resource-model scope."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.cluster.path_catalog import topology_id
from fluidckpt.cluster.service_cost import estimate_execution, new_state


def probe():
    workers = [dict(worker=i, node=f"symbolic_{i}", gpu=0, numa=0,
                    session="independent_cpu_review", epoch=1) for i in range(8)]
    model = dict(schema_version=1, topology_id=topology_id(workers), executor="serial",
                 chunk_bytes=32 << 20, dispatch_s=0.,
                 scope="SYMBOLIC dependency probe; no measured coefficients or shared contention",
                 paths={"remote": dict(warm_GBps=1., operation_s=0., first_pair_s=0.),
                        "local": dict(warm_GBps=4., operation_s=0., first_pair_s=0.)})

    def score(ops):
        plan = dict(ops=ops, executor="serial", chunk_bytes=32 << 20,
                    estimated_transfer_s=0.)
        return estimate_execution(plan, workers, model, new_state(workers))

    def op(name, src, dst, size, path="remote"):
        return dict(op_id=name, src_w=src, dst_w=dst, n=size, path=path)

    independent = score([op(f"remote/{i}", i + 4, i, 8_000_000_000) for i in range(4)])
    single_source = score([op(f"single/{i}", 4, i, 8_000_000_000) for i in range(4)])
    local_then_remote = score([item for i in range(4) for item in (
        op(f"local/{i}", i, i, 4_000_000_000, "local"),
        op(f"tail/{i}", i + 4, i, 4_000_000_000))])
    shared_cut_lower_bound_s = 32_000_000_000 / 1_000_000_000
    checks = {
        "independent_pairs_really_overlap_in_existing_scorer": independent["estimated_execute_s"] == 8.,
        "single_source_is_not_four_independent_channels": single_source["estimated_execute_s"] == 32.,
        "local_then_remote_same_target_is_serial_five_not_four": local_then_remote["estimated_execute_s"] == 5.,
        "distinct_workers_do_not_make_shared_cut_independent": shared_cut_lower_bound_s > independent["estimated_execute_s"],
    }
    return dict(checks=checks, results=dict(independent=independent, single_source=single_source,
                                          local_then_remote=local_then_remote,
                                          shared_cut_lower_bound_s=shared_cut_lower_bound_s),
                qualification="symbolic_dependency_scope_verified_only",
                measured_rate_ready=False, real_model_T_ready_measured=False,
                new_GPU_samples=0, SSH_actions=0, product_mutations=0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    result = probe()
    refs = [Path(__file__).relative_to(ROOT).as_posix(),
            "main_lib/fluidckpt/cluster/service_cost.py",
            "main_lib/fluidckpt/cluster/path_catalog.py"]
    result["source_refs"] = [dict(path=p, sha256=hashlib.sha256((ROOT / p).read_bytes()).hexdigest()) for p in refs]
    (args.out / "checks.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if not all(result["checks"].values()):
        raise SystemExit("Independent dependency check failed")
    print(json.dumps(dict(passed=len(result["checks"]), out=str(args.out)), ensure_ascii=False))


if __name__ == "__main__":
    main()
