"""Review the current node28 metadata and the preserved failed node30 run locally."""
import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / "codex_coding/results/v0.1.23.3/task2/three_node_recovery"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    admission_path = RESULTS / "admission28_refresh_r01/stdout.json"
    failed_path = RESULTS / "audit_failed_node30_init_r01/summary.json"
    admission = json.loads(admission_path.read_text())
    failed = json.loads(failed_path.read_text())
    refs = failed["input_refs"]
    checks = {
        "old30_refs_unchanged": all(digest(Path(r["path"])) == r["sha256"] for r in refs),
        "old30_failed_runtime_preserved": failed["runtime_passed"] is False and failed["hip_free_bytes"] is None,
        "old30_cleanup_not_runtime_pass": failed["exact_cleanup_verified"] is True and failed["payload_bytes"] == 0,
        "new28_metadata_only": all(admission[k] == 0 for k in ("gpu_api_calls", "docker_creations", "signals", "node27_accesses", "node29_accesses")),
        "new28_protected_containers": len(admission["protected_containers"]) == 2 and all(all(r["checks"].values()) for r in admission["protected_containers"]),
        "new28_protected_processes": len(admission["protected_processes"]) == 13 and all(r["owner_matches"] and r["state"] != "Z" for r in admission["protected_processes"]),
        "new28_six_catalogs_idle": len(admission["keepers"]) == 6 and all(r["passed"] and not r["body"]["transactions"] and all(x["leases"] == 0 for x in r["body"]["replicas"]) for r in admission["keepers"]),
        "new28_sysfs_not_fresh_hipfree": admission["selected_gpu"]["hip_free_bytes"] is None,
        "new28_selected_bdf": admission["selected_gpu"]["physical_gpu"] == 2 and admission["selected_gpu"]["bdf"] == "0000:55:00.0",
        "new28_docker_root": admission["docker_root"]["returncode"] == 0 and json.loads(admission["docker_root"]["stdout"]) == "/var/lib/docker",
    }
    result = {
        "evidence_consistent": all(checks.values()), "checks": checks,
        "old_node30_runtime_passed": False,
        "node28_fresh_runtime_init": None, "node28_copy": None,
        "xml_runtime_accepted": None, "performance_ready": False,
        "node28_sample_utc": admission["started_utc"],
        "node28_df": admission["df"],
        "node28_protected_process_count": 13,
        "node28_replica_count": sum(len(r["body"]["replicas"]) for r in admission["keepers"]),
        "node28_catalog_digests": {r["endpoint"]: r["catalog_sha256"] for r in admission["keepers"]},
        "input_refs": [{"path": str(p), "sha256": digest(p)} for p in (admission_path, failed_path)],
        "scope": "Local original evidence review; no SSH, Docker, GPU or signals. Metadata does not certify fresh init or full weight content.",
        "review_note": "Old node30 command fixed OMP_NUM_THREADS and MKL_NUM_THREADS to 1; new comparisons must preserve them. The existing host wrapper had omitted these exec overrides.",
    }
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "source.py").write_bytes(Path(__file__).read_bytes())
    (args.out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"evidence_consistent": result["evidence_consistent"], "checks": len(checks), "out": str(args.out)}))
    if not result["evidence_consistent"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
