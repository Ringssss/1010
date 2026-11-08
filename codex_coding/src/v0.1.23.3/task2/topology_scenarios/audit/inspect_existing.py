"""Read local evidence without contacting retained workers or changing inputs."""
import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compact(value, depth=0):
    if depth > 3:
        return {"type": type(value).__name__, "length": len(value)} if isinstance(value, (dict, list)) else value
    if isinstance(value, dict):
        return {k: compact(v, depth + 1) for k, v in value.items() if k not in {"snapshots", "alpha_beta", "single", "concurrent", "catalog", "inventories", "capture", "snapshot", "base_catalog"}}
    if isinstance(value, list):
        return [compact(v, depth + 1) for v in value[:8]] + ([{"omitted": len(value) - 8}] if len(value) > 8 else [])
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    names = [
        "codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json",
        "codex_coding/results/v0.1.23.3/task2/paths01/network_pairs.json",
        "codex_coding/results/v0.1.23.3/task2/costcal01/service_model.json",
        "codex_coding/results/profile/profile_node27.json",
        "codex_coding/results/profile/profile_node27_longlived_worker.json",
        "codex_coding/results/profile/profile_node27_heldout.json",
        "codex_coding/results/v0.1.23.3/task2/basic_scenarios/evidence_r01/report.md",
        "codex_coding/results/v0.1.23.3/task2/basic_scenarios/s5_control_review_r01/report.md",
    ]
    result = {"schema": "topology_existing_evidence.v1", "new_gpu_samples": 0, "inputs": []}
    for name in names:
        path = args.root / name
        item = {"path": name, "exists": path.exists()}
        if path.exists():
            item["sha256_before"] = digest(path)
            if path.suffix == ".json":
                value = json.loads(path.read_text())
                if "session_overlay" in name:
                    item["summary"] = {k: value.get(k) for k in ("members", "endpoints", "nodes", "profile")}
                    item["worker_summary"] = [{k: w.get(k) for k in ("worker", "node", "gpu", "numa", "endpoint")} for w in value.get("workers", [])]
                else:
                    item["summary"] = compact(value)
                    item["single_count"] = len(value.get("single", []))
                    item["concurrent_case_ids"] = list(value.get("concurrent", {}))
            item["sha256_after"] = digest(path)
            item["unchanged"] = item["sha256_before"] == item["sha256_after"]
        result["inputs"].append(item)
    result["inputs_unchanged"] = all(x.get("unchanged", True) for x in result["inputs"])
    (args.out / "existing_evidence.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(args.out), "inputs_unchanged": result["inputs_unchanged"], "new_gpu_samples": 0}))


if __name__ == "__main__":
    main()
