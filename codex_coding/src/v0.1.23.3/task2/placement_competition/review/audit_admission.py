"""Read-only admission checks; do not refresh remote state or launch workers."""
import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[6]
FEASIBILITY = ROOT / "codex_coding/results/v0.1.23.3/task2/tp4_scenarios/feasibility_r03/deployment_feasibility.json"


def admission_checks():
    data = json.loads(FEASIBILITY.read_text())
    checks = {
        "prior_physical_admission_false": data["ready"] is False,
        "prior_gpu_authorization_false": data["GPU_execution_authorized"] is False,
        "prior_gpu_calls_zero": data["GPU_calls"] == data["new_GPU_workers"] == 0,
        "prior_no_remote_writes_or_signals": data["remote_writes"] == 0 and data["signals"] == [],
    }
    summaries = []
    for candidate in data["candidates"]:
        name = candidate["candidate_id"]
        checks[name + "/not_admitted"] = candidate["ready"] is False
        checks[name + "/no_launch_command"] = candidate["launch_command"] is None
        checks[name + "/gpu_unauthorized"] = candidate["GPU_execution_authorized"] is False
        checks[name + "/no_full_peak_proof"] = candidate["full_physical_peak_verified"] is False
        summaries.append({"candidate_id": name, "worker_count": candidate["worker_count"],
                          "equivalence": candidate["equivalence"], "blocker": candidate["blocker"]})
    reused = next(c for c in data["candidates"] if c["worker_count"] == 4)
    checks["four_worker_reuse_changes_dependency_problem"] = reused["equivalence"] == "not_proven_changed_DAG"
    checks["keeper_reuse_not_admitted"] = data["keeper_reuse"]["admissible"] is False
    checks["old_loaded_code_sha_unknown"] = data["keeper_reuse"]["loaded_old_product_code_sha256"] is None
    return {"checks": checks, "prior_deployment_candidates": summaries,
            "qualification": "offline_review_only_physical_unadmitted",
            "physical_budget_admitted": False, "gpu_command": None,
            "review_GPU_calls": 0, "review_SSH_actions": 0,
            "review_remote_writes": 0, "review_signals": 0,
            "source_refs": [{"path": str(p.relative_to(ROOT)),
                             "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                            for p in (FEASIBILITY, Path(__file__))]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    result = admission_checks()
    (args.out / "qualification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": sum(result["checks"].values()), "total": len(result["checks"]),
                      "out": str(args.out)}, ensure_ascii=False))
    if not all(result["checks"].values()):
        raise SystemExit("Admission evidence review failed")


if __name__ == "__main__":
    main()
