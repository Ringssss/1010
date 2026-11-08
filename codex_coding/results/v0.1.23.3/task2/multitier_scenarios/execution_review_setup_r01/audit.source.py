"""Independently qualify static mapping and empty-container setup only."""
import argparse
import ast
import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
BASE = ROOT / "codex_coding/results/v0.1.23.3/task2/multitier_scenarios"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapping", type=Path, required=True)
    ap.add_argument("--admission", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    checks, refs = {}, []
    def read(path):
        path = path.resolve()
        refs.append({"path": str(path), "sha256": sha(path)})
        return json.loads(path.read_text())
    binding = read(args.mapping / "machine_binding.json")
    spec = read(args.admission / "node27.setup.spec.json")
    plan = read(args.admission / "plan.json")
    hosts = {r["node"]: r for r in binding["hosts"]}
    raw = {}
    pci = {}
    for node in ("node27", "node28", "node30"):
        raw[node] = read(BASE / "mapping_refresh_r01" / (node + ".stdout.json"))
        pci[node] = read(BASE / "mapping_pci_followup_r01" / (node + ".stdout.json"))
        checks[node + "/same_boot_across_metadata"] = raw[node]["boot_id"] == pci[node]["boot_id"] == hosts[node]["boot_id"]
        checks[node + "/eight_DCU_class_not_ASPEED"] = len(pci[node]["gpus"]) == 8 and all(
            g["pci_chain"][0]["vendor"] == "0x1d94" and g["pci_chain"][0]["device"] == "0x6320" and
            g["pci_chain"][0]["class"] == "0x0b4000" for g in pci[node]["gpus"])
        checks[node + "/metadata_not_runtime_admission"] = raw[node]["runtime_admission"] is False and pci[node]["runtime_admission"] is False
        checks[node + "/no_GPU_remote_write_or_node29"] = all(raw[node][k] == 0 for k in ("gpu_calls", "remote_writes", "node29_accesses"))
        for role in hosts[node]["roles"]:
            actual = next(g for g in pci[node]["gpus"] if g["bdf"] == role["bdf"])
            checks[node + "/" + role["logical_gpu"] + "/actual_BDF_NUMA"] = role["numa"] == int(actual["pci_chain"][0]["numa_node"])
        ssd = hosts[node]["ssd"]
        source = next(x for x in raw[node]["storage_paths"] if x["path"] == ssd["verified_existing_parent"])
        disk = next(x for x in raw[node]["block_devices"] if x["name"] == ssd["physical_disk"])
        checks[node + "/SSD_real_local_block_mount"] = source["mount"] == ssd["mount"] and source["mount"]["fstype"] in ("ext4", "xfs") and source["mount"]["source"].startswith("/dev/")
        checks[node + "/SSD_capacity_independent"] = ssd["device_size_bytes"] == int(disk["size_512b_sectors"]) * 512 and ssd["rotational"] == "0"
    checks["node27/new_boot_and_old_exact_PID_absent"] = binding["node27_old_pending"]["boot_changed"] is True and raw["node27"]["pending_exact"][0]["old_process_absent_at_sample"] is True
    checks["node27/old_exit_code_not_invented"] = raw["node27"]["pending_exact"][0]["exit_status_recovered"] is False
    checks["node27/three_old_containers_stopped"] = len(raw["node27"]["containers"]) == 3 and all(c["state"]["Running"] is False for c in raw["node27"]["containers"])
    checks["setup/current_host_boot_image"] = spec["boot_id"] == raw["node27"]["boot_id"] and spec["hostname"] == raw["node27"]["hostname"] and spec["image_id"] == "sha256:ee3b9807d9e90cdc3e6ab9c1058f0b78dabcafa6a0fc7661d9c5af69ce1f9588"
    source = args.admission / "fresh_host27.source.py"
    original = args.admission / "fresh_host.original.py"
    prepared = source.read_text()
    changes = [('spec["node"] not in ("node28","node30")', 'spec["node"] not in ("node27",)'),
               ('r"zjn-fluid-three-node-[a-z0-9_]+-n(28|30)"', 'r"zjn-fluid-three-node-multitier_admission27_r01-n27"'),
               ('r"[a-z0-9_]+_n(28|30)"', 'r"multitier_admission27_r01_n27"'),
               ('"--memory","8g"', '"--memory","6g"'),
               ('if mode=="guard":', 'if mode=="guard":\n        raise SystemExit("node27 adapter excludes driver-property guard; use read-only host_metadata instead")')]
    reconstructed = original.read_text()
    for old, new in changes:
        checks["setup/replacement_exactly_once/" + str(len(checks))] = reconstructed.count(old) == 1
        reconstructed = reconstructed.replace(old, new)
    checks["setup/exact_five_registered_changes"] = reconstructed == prepared
    ast.parse(prepared)
    checks["setup/source_SHA_exact"] = sha(source) == spec["host_wrapper_sha256"]
    checks["setup/no_GPU_or_NCCL_launch_in_setup_argv"] = plan["setup_argv"][0] == "python3" and plan["setup_argv"][2] == "setup"
    checks["setup/protected_names_complete"] = set(spec["protected_containers"]) == {c["name"].lstrip("/") for c in raw["node27"]["containers"]}
    checks["setup/no_keeper_or_known_worker_adoption"] = spec["keeper_endpoints"] == [] and spec["known_live_workers"] == []
    checks["setup/existence_guards_no_pull_private_IPC_core0"] = all(t in prepared for t in ('stage.exists()', 'stage already exists; no overwrite', 'container inventory failed/name already exists', '"--pull=never"', '"--ipc","private"', '"core=0:0"'))
    runtime = args.admission / "init_gate_diag60.source.py"
    checks["copy/frozen_successful_runtime_exact"] = sha(runtime) == plan["copy_runtime_source_sha256"] == sha(ROOT / plan["copy_runtime_source"])
    checks["copy/payload_deadline_scope"] = plan["payload_bytes"] == 1 << 20 and plan["copy_inner_deadline_seconds"] == 60 and plan["copy_outer_timeout_seconds"] == 75 and plan["stage"] == "copy"
    checks["copy/asset_exact_source_SHA"] = len(spec["assets"]) == 1 and hashlib.sha256(base64.b64decode(spec["assets"][0]["base64"], validate=True)).hexdigest() == sha(runtime)
    checks["binding/physical_ready_false"] = binding["physical_ready"] is False
    for path in (source, original, runtime):
        refs.append({"path": str(path.resolve()), "sha256": sha(path)})
    result = {"qualification": "static_mapping_and_empty_setup_passed" if all(checks.values()) else "failed",
              "passed": all(checks.values()), "checks": checks, "check_count": len(checks),
              "approved_action_scope": "node27 exact empty sleep container setup only",
              "GPU_copy_execution_admitted": False, "six_worker_execution_admitted": False,
              "physical_ready": False, "evidence_refs": refs}
    (args.out / "qualification.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    (args.out / "audit.source.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"passed": result["passed"], "check_count": len(checks), "out": str(args.out)}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
