"""Independently check explicit worker and shared-resource witnesses."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[6]


def simple_queue(operations):
    ready = {i: 0 for i in range(6)}
    starts = []
    for source, target in operations:
        begin = max(ready[source], ready[target])
        starts.append(begin)
        ready[source] = ready[target] = begin + 1
    return starts, max(ready.values())


def audit_objective(directory):
    data = json.loads((directory / "checks.json").read_text())
    checks = {"objective_source_hashes_match": all(
        hashlib.sha256((ROOT / r["path"]).read_bytes()).hexdigest() == r["sha256"]
        for r in data["source_refs"])}
    for name, operations in {
        "two_helpers": [(4, 0), (5, 1), (4, 2), (5, 3)],
        "reused_roles": [(0, 0), (1, 1), (0, 2), (1, 3)],
        "local_first_worker_dag": [(0, 0), (1, 1), (4, 0), (5, 1)],
    }.items():
        starts, completion = simple_queue(operations)
        checks[name + "/independent_starts"] = starts == [row["start_s"] for row in data[name]["timeline"]]
        checks[name + "/independent_completion"] = completion == data[name]["estimated_execute_s"] == 2
        checks[name + "/independent_ends"] = [s + 1 for s in starts] == [row["end_s"] for row in data[name]["timeline"]]
    shared = data["fixed_local_first_shared_resource"]
    witness = shared["feasible_witness"]
    all_conflicts_absent = True
    for a, b in itertools.combinations(witness, 2):
        overlap = max(a["start"], b["start"]) < min(a["end"], b["end"])
        if overlap and (set(a["workers"]) & set(b["workers"]) or set(a["resources"]) & set(b["resources"])):
            all_conflicts_absent = False
    checks["shared_witness_worker_and_resource_nonoverlap"] = all_conflicts_absent
    checks["unit_work_duration_and_order"] = (all(row["end"] - row["start"] == 1 for row in witness)
                                               and witness[0]["end"] <= witness[2]["start"]
                                               and witness[1]["end"] <= witness[3]["start"])
    release = max(witness[0]["end"], witness[1]["end"])
    remote_work = sum(row["end"] - row["start"] for row in witness if "U" in row["resources"])
    release_cut_bound = release + remote_work
    checks["independent_release_cut_and_witness_match_three"] = (release_cut_bound == max(row["end"] for row in witness)
                                                                      == shared["fixed_queue_release_cut_bound"] == 3)
    checks["separate_bound_and_dag_max_unattainable"] = max(shared["cut_load_bound"], shared["worker_dag_score"]) == 2 < release_cut_bound
    checks["scope_flags_preserved"] = (data["actual_T_ready_measured"] is False
                                       and data["shared_contention_calibrated"] is False
                                       and data["new_GPU_samples"] == data["SSH_actions"] == data["product_mutations"] == 0)
    return {"checks": checks, "fixed_shared_queue_exact_symbolic_completion": release_cut_bound,
            "scope": "Explicit unit-work witnesses only; no general shared-resource scheduler or execution optimum.",
            "GPU_calls": 0, "SSH_actions": 0, "product_mutations": 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--objective-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    result = audit_objective(args.objective_dir)
    result["source_refs"] = [{"path": str(p.resolve().relative_to(ROOT)),
                              "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                             for p in [args.objective_dir / "checks.json", Path(__file__)]]
    result["qualification"] = "passed_explicit_objective_scope" if all(result["checks"].values()) else "failed"
    (args.out / "qualification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": sum(result["checks"].values()), "total": len(result["checks"])}))
    if not all(result["checks"].values()):
        raise SystemExit("Objective review failed")


if __name__ == "__main__":
    main()
