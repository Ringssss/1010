"""Separate artifact correctness from meaningful multi-location acceptance."""
import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[6]


def gate(directory):
    contract = json.loads((directory / "contract.json").read_text())
    comparison = json.loads((directory / "comparison.json").read_text())
    case = contract["case"]
    used = {op["replica_id"] for row in comparison["finite_layouts"] for op in row["plan"]["ops"]}
    paths = {p["replica_id"]: p for p in contract["path_contract"]}
    candidate_ids = {c["id"] for c in case["candidates"]}
    ranks = []
    for rank in range(4):
        candidates = [c for c in case["candidates"] if c["tp"] == rank]
        fallback = max(paths[b["id"]]["symbolic_rate_units_per_time"] for b in case["base"] if b["tp"] == rank)
        meaningful = [c["id"] for c in candidates if c["id"] in used]
        ranks.append({"rank": rank, "legal_candidate_ids": [c["id"] for c in candidates],
                      "used_candidate_ids_in_complete_finite_reference": meaningful,
                      "unused_candidate_ids": [c["id"] for c in candidates if c["id"] not in used],
                      "best_dram_fallback_rate_units": fallback,
                      "candidate_rate_units": {c["id"]: paths[c["id"]]["symbolic_rate_units_per_time"] for c in candidates},
                      "at_least_two_used_location_choices": len(meaningful) >= 2})
    accepted = all(row["at_least_two_used_location_choices"] for row in ranks)
    return {"artifact_correctness_separate_from_research_gate": True,
            "multi_location_candidate_legality": all(len(row["legal_candidate_ids"]) >= 2 for row in ranks),
            "every_block_multiple_meaningful_locations_gate": accepted,
            "all_candidates_used_somewhere": candidate_ids <= used,
            "rank_findings": ranks,
            "unused_candidates": sorted(candidate_ids - used),
            "qualification": "multiple_meaningful_sources_gate_passed" if accepted else "multiple_meaningful_sources_gate_failed_negative_result",
            "scope": "Meaningful evidence is actual use under at least one complete frozen feasible layout and common routing policy. Legal slow paths alone do not establish placement competition.",
            "physical_budget_admitted": False, "gpu_command": None,
            "GPU_calls": 0, "SSH_actions": 0, "product_mutations": 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    result = gate(args.case_dir)
    result["source_refs"] = [{"path": str(p.resolve().relative_to(ROOT)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                             for p in (args.case_dir / "contract.json", args.case_dir / "comparison.json", Path(__file__))]
    (args.out / "qualification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"gate_passed": result["every_block_multiple_meaningful_locations_gate"],
                      "unused_candidates": result["unused_candidates"]}))


if __name__ == "__main__":
    main()
