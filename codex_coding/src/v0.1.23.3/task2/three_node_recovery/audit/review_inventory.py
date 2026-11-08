"""Review frozen three-node inventories from local artifacts, without remote calls."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / "codex_coding/results/v0.1.23.3/task2/three_node_recovery"
NODES = {"node27", "node28", "node30"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scope", nargs="+", choices=sorted(NODES), default=sorted(NODES))
    args = parser.parse_args()
    args.inventory = args.inventory.resolve()
    args.out = args.out.resolve()
    if args.inventory.parent != RESULTS or args.out.parent != RESULTS or not args.out.name.startswith("audit_"):
        parser.error("require local versioned inventory and a fresh audit directory")
    manifest_path = args.inventory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    scope = set(args.scope)
    complete_scope = scope == NODES
    rows = [r for r in manifest["commands"] if r["node"] in scope]
    checks = {
        "scoped_node_records": {r["node"] for r in rows} == scope and len(rows) == len(scope),
        "manifest_zero_gpu_node29_accesses": all(manifest.get(k) == 0 for k in ("gpu_api_calls", "node29_accesses")),
        "frozen_host_source": sha(args.inventory / "host_readonly.source.py") == manifest["source_sha256"],
        "strict_remote_hostkeys": all("StrictHostKeyChecking=yes" in r["argv"] and "UpdateHostKeys=no" in r["argv"] for r in rows if r["node"] != "node27"),
    }
    if complete_scope:
        checks["finished_inventory"] = bool(manifest.get("ended_utc"))
        checks["known_hosts_unchanged"] = manifest["known_hosts_before"] == manifest.get("known_hosts_after") and manifest.get("known_hosts_unchanged") is True
        checks["frozen_files"] = bool(manifest.get("files")) and all(Path(name).name == name and sha(args.inventory / name) == expected for name, expected in manifest.get("files", {}).items())
    summaries = []
    for row in rows:
        node = row["node"]
        snapshot = args.inventory / f"{node}.stdout.json"
        if row.get("returncode") != 0:
            summaries.append({"node": node, "collected": False, "returncode": row.get("returncode"), "reason": (args.inventory / f"{node}.stderr.txt").read_text()[-500:]})
            continue
        data = json.loads(snapshot.read_text())
        prior = ROOT / f"codex_coding/results/v0.1.23.3/task2/resource_map/connectivity_nodes_r01/ssh_12_11_3_{node[4:]}.json"
        identity = json.loads(prior.read_text())
        prior_fields = identity["stdout"].splitlines()
        checks[f"{node}/hostname"] = identity["returncode"] == 0 and data["hostname"] == prior_fields[0]
        checks[f"{node}/host_source"] = data["collector_source_sha256"] == manifest["source_sha256"]
        checks[f"{node}/gpu_api_zero"] = data["gpu_api_calls"] == 0 and data["signals"] == 0 and data["node29_accesses"] == 0
        bdfs = [r["pci_chain"][0]["bdf"] for r in data["drm"] if r.get("pci_chain")]
        checks[f"{node}/unique_gpu_bdfs"] = len(bdfs) == len(set(bdfs)) == len(data["drm"])
        free = []
        for gpu in data["drm"]:
            total, used = gpu.get("mem_info_vram_total"), gpu.get("mem_info_vram_used")
            if isinstance(total, str) and isinstance(used, str):
                checks[f"{node}/{gpu['card']}/sysfs_arithmetic"] = int(total) - int(used) == gpu.get("total_minus_used_bytes")
            free.append({"card": gpu["card"], "bdf": gpu["pci_chain"][0]["bdf"] if gpu.get("pci_chain") else None, "sysfs_total_minus_used_bytes": gpu.get("total_minus_used_bytes"), "hip_free_bytes": None})
        checks[f"{node}/owner_unknown_boundary"] = data["kfd_proc_visibility"]["zero_does_not_prove_no_owners"] is True
        summaries.append({"node": node, "collected": True, "hostname": data["hostname"], "boot_id": data["boot_id"], "boot_continuous_with_prior_ssh": data["boot_id"] == prior_fields[1], "drm_device_count": len(data["drm"]), "kfd_gpu_count": len(data["kfd"]), "hca_count": len(data["hca"]), "numa_count": len(data["numa"]), "owner_inventory_complete": False, "kfd_visible_process_count": len(data["kfd_processes"]), "read_errors": len(data["errors"]), "drm_devices": free})
    result = {"evidence_consistent": all(checks.values()), "checks": checks, "failed_checks": [k for k, v in checks.items() if not v], "nodes": summaries, "inventory_ref": {"path": str(manifest_path), "sha256": sha(manifest_path)}, "collection_complete": bool(manifest.get("ended_utc")), "node_scope": sorted(scope), "excluded_node_status_unreviewed": sorted(NODES-scope), "gpu_operations": 0, "runtime_ready": False, "performance_ready": False, "scope": "local frozen source, raw host identity and sysfs arithmetic only; no HIP free, owner exclusivity or budget certification"}
    delta_path = args.inventory / "partial_completion.json"
    if delta_path.exists():
        delta = json.loads(delta_path.read_text())
        result["operation_delta_ref"] = {"path": str(delta_path), "sha256": sha(delta_path)}
        result["collection_manual_signals_sent"] = delta["manual_signals_sent"]
        result["collection_automatic_timeout_sigkill_attempts"] = delta["automatic_subprocess_timeout_sigkill_attempts"]
        result["collection_own_D_exited_verified"] = delta["own_D_exited_verified"]
        result["stale_manifest_signal_count_is_not_final"] = True
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "source.py").write_bytes(Path(__file__).read_bytes())
    frozen = args.out / "inputs"
    frozen.mkdir()
    inputs = [manifest_path, args.inventory / "host_readonly.source.py", args.inventory / "collect.source.py"]
    if delta_path.exists():
        inputs.append(delta_path)
    inputs.extend(args.inventory / f"{node}.stdout.json" for node in scope if (args.inventory / f"{node}.stdout.json").exists())
    result["frozen_input_refs"] = []
    for path in inputs:
        saved = frozen / path.name
        saved.write_bytes(path.read_bytes())
        result["frozen_input_refs"].append({"original": str(path), "saved": str(saved), "sha256": sha(saved)})
    (args.out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"evidence_consistent": result["evidence_consistent"], "failed_checks": result["failed_checks"], "nodes": [{k: v for k, v in n.items() if k != "drm_devices"} for n in summaries]}, ensure_ascii=False))
    if not result["evidence_consistent"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
