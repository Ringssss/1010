"""Check that the node28 LD candidate changes one environment value only."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex

RESULTS = Path(__file__).resolve().parents[6] / "codex_coding/results/v0.1.23.3/task2/three_node_recovery"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    paths = [RESULTS / "admission28_launch_init_r01/launch.json", RESULTS / "diagnosis30_node28_ldplan_r01/launch.json", RESULTS / "diagnosis30_node28_ldplan_r01/stdout.json"]
    baseline, variant, metadata = [json.loads(p.read_text()) for p in paths]
    normalized = list(variant["remote_argv"])
    idx = normalized.index("LD_LIBRARY_PATH=" + variant["variant"])
    prefix = normalized[idx - 1]
    del normalized[idx - 1:idx + 1]
    normalized[normalized.index("--out") + 1] = baseline["remote_argv"][baseline["remote_argv"].index("--out") + 1]
    checks = {
        "exact_argv_except_one_ld_and_unique_out": prefix == "-e" and normalized == baseline["remote_argv"],
        "outer_remote_quoting_matches": shlex.split(variant["argv"][-1]) == variant["remote_argv"],
        "ld_original_order_preserved": variant["variant"].replace(variant["added_directory"] + ":", "", 1) == variant["inherited"],
        "metadata_inherited_env": metadata["static_env"]["env_whitelist"]["LD_LIBRARY_PATH"] == variant["inherited"],
        "readonly_current_identity": metadata["identity_passed"] and metadata["gpu_operations"] == metadata["remote_file_writes"] == 0,
        "baseline_ref_sha": sha(paths[0]) == variant["baseline_launch_sha256"],
        "metadata_ref_sha": sha(paths[2]) == variant["metadata_input_sha256"],
        "same_deadline_payload": variant["deadline_seconds"] == 15 and variant["payload_bytes"] == 0,
    }
    result = {"plan_review_passed": all(checks.values()), "checks": checks, "runtime_passed": None, "performance_ready": False, "scope": "One node28 LD path candidate plan only; no source env.sh, GPU execution or causality claim by this reviewer.", "input_refs": [{"path": str(p), "sha256": sha(p)} for p in paths]}
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "source.py").write_bytes(Path(__file__).read_bytes())
    (args.out / "summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"plan_review_passed": result["plan_review_passed"], "checks": len(checks)}))
    if not result["plan_review_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
