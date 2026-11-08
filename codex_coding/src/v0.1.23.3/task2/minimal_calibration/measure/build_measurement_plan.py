"""Build exact small workpoints after parent-created container identities exist."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
REL = "codex_coding/src/v0.1.23.3/task2/minimal_calibration/measure/measurement_driver.py"
RT = "codex_coding/src/v0.1.23.3/task2/three_node_recovery/admission28/runtime_gate_r03.py"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--setup-plan", type=Path, required=True)
    parser.add_argument("--node28-id", required=True)
    parser.add_argument("--node28-started", required=True)
    parser.add_argument("--node30-id", required=True)
    parser.add_argument("--node30-started", required=True)
    parser.add_argument("--qualification-ref", required=True)
    parser.add_argument("--node28-guard-ref", required=True)
    parser.add_argument("--node30-guard-ref", required=True)
    parser.add_argument("--master-port", type=int, default=29910)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    source = (ROOT / REL).read_bytes()
    rt_sha = hashlib.sha256((ROOT / RT).read_bytes()).hexdigest()
    (args.out / "measurement_driver.source.py").write_bytes(source)
    rows = []
    for size in (64, 256):
        for primitive in ("local_numa_h2d", "remote_numa_h2d_rccl_broadcast"):
            for rank, node in enumerate(("node28", "node30")):
                spec = json.loads((args.setup_plan / (node + ".spec.json")).read_text())
                n = node[-2:]
                config = {"node": node, "hostname": spec["hostname"], "boot_id": spec["boot_id"],
                    "container_id": getattr(args, node + "_id"), "container_started_at": getattr(args, node + "_started"),
                    "physical_gpu": spec["gpu"], "bdf": spec["bdf"], "primitive": primitive,
                    "payload_bytes": size << 20, "iterations": 1, "flow_count": 1,
                    "source_node": "node28" if primitive.startswith("remote") else node,
                    "source_numa": 0 if primitive.startswith("remote") or node == "node28" else 4,
                    "source_resource_id": "node28/numa/0" if primitive.startswith("remote") or node == "node28" else "node30/numa/4",
                    "source_gpu_resource_id": "node28/pci/0000:09:00.0" if primitive.startswith("remote") else None,
                    "target_resource_id": node + "/pci/" + spec["bdf"],
                    "runtime_source": str(ROOT / RT), "runtime_source_sha256": rt_sha, "main_lib_root": str(ROOT / "main_lib"),
                    "load_scope": "guarded_candidate_scope", "qualification_ref": args.qualification_ref,
                    "guard_before_ref": getattr(args, node + "_guard_ref"),
                    "owner_refs": [str(ROOT / f"codex_coding/results/v0.1.23.3/task2/minimal_calibration/measure_refresh_r01/{node}/stdout.json"),
                                   str(ROOT / "codex_coding/results/v0.1.23.3/task2/minimal_calibration/measure_owner_scope_r01/stdout.json")],
                    "host_guard_bytes": 2 << 30, "gpu_guard_bytes": 2 << 30,
                    "incremental_HBM_limit_bytes": 2 << 30, "incremental_DRAM_limit_bytes": 2 << 30,
                    "planner_profile_eligible": False, "production_executor_equivalent": False}
                if primitive.startswith("remote"):
                    config.update(rank=rank, master=f"tcp://12.11.3.28:{args.master_port}",
                        xml_manifest=str(ROOT / f"codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_xml_2830_gdr0_r01/{node}.manifest.json"))
                case = f"{'remote' if primitive.startswith('remote') else 'local'}_{size}MiB_{node}"
                path = args.out / (case + ".config.json")
                path.write_text(json.dumps(config, indent=2) + "\n")
                rows.append({"case_id": case, "config": str(path), "config_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                             "driver_sha256": hashlib.sha256(source).hexdigest(), "requires_parent_launch": True})
    (args.out / "manifest.json").write_text(json.dumps({"rows": rows, "samples_per_configuration": 1,
        "single_flow_only": True, "formal_performance_ready": False, "planner_profile_eligible": False,
        "production_chain_equivalent": False, "actual_product_recovery_required_separately": True,
        "budgets": {"container_bytes": 8 << 30, "incremental_HBM_bytes_per_node": 2 << 30,
                    "incremental_DRAM_bytes_per_node": 2 << 30}}, indent=2) + "\n")
    print(json.dumps({"out": str(args.out), "workpoint_count": len(rows), "gpu_calls": 0}))


if __name__ == "__main__":
    main()
