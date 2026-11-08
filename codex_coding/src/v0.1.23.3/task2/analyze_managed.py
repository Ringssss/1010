"""Audit the managed placement artifact and report model prediction error separately from readiness."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT/"main_lib"))
sys.path.insert(0, str(ROOT/"codex_coding/src/task2/cluster"))
from fluidckpt.cluster.managed import verify_plan_hash, fits
from analyze_restore_profile import loading


def audit_plan(plan, journal):
    verify_plan_hash(plan)
    assert journal["plan_id"] == plan["plan_id"] and journal["status"] == "applied"
    actions = plan["transition"]["actions"]
    assert journal["next_action"] == len(actions) == len(journal["completed"])
    previous = plan["initial_state"]
    for index, action in enumerate(actions):
        assert action["before_state"] == previous
        assert fits(action["usage"], plan["limits"], peak=True)
        assert action["replica"]["id"] not in plan["request"]["base_ids"]
        completed = journal["completed"][index]
        assert completed["index"] == index and completed["after_state"] == action["after_state"]
        if action["kind"] == "build" and not completed["recovered_after_publication"]:
            assert completed["result"]["verified_bytes"] == action["replica"]["n"]
        previous = action["after_state"]
    assert previous == journal["expected_state"] == plan["transition"]["final_state"]
    assert fits(journal["final_usage"], plan["limits"])


def main(directory):
    raw = (directory/"summary.json").read_bytes(); data = json.loads(raw)
    fixture = data["managed_fixture"]
    audit_plan(fixture["plan"], fixture["resumed"])
    assert fixture["restored"]["verification"]["passed"]
    assert fixture["interrupted"]["status"] == "interrupted"
    assert fixture["stale_rejected"] and fixture["idempotent"] and fixture["repeat_decision"] == "keep_current"
    report = {"development": data["development"], "run": data["run"], "status": data["status"],
        "validation_topology": data.get("validation_topology"),
        "summary_sha256": hashlib.sha256(raw).hexdigest(), "fixture": {
            "verified_bytes": fixture["restored"]["verification"]["checked_bytes"],
            "actions": len(fixture["plan"]["transition"]["actions"]),
            "interruption_resumed": True, "stale_rejected": True, "idempotent": True, "stable_replan": True},
        "models": [], "limitations": ["single model, single known restore scenario, serial executor",
            "an immutable base must cover all demanded bytes; complete-base migration is not implemented",
            "one coordinator; external catalog mutations cause rejection, not distributed atomic rollback",
            "headroom is sampled before actions, not globally reserved against other workloads",
            "reported peak is managed replica allocation accounting, not a sampled physical memory high-water mark",
            "one model restore, no paired speedup claim; path profile remains provisional"]}
    if not data.get("validation_topology", {}).get("cross_node_validated", False):
        report["limitations"].append("this run uses one host; cross-node managed-controller validation is pending occupied remote GPUs")
    if "managed_plan" in data:
        p = data["managed_plan"]; applied = data["managed_apply"]["journal"]
        audit_plan(p, applied)
        assert not p["fallback_reason"]
        assert sum(r["n"] for r in p["candidates"]["coverage"]) == sum(t["n"] for t in p["request"]["targets"])
        report["placement"] = {"candidate_summary": p["candidates"], "decision": p["decision"], "solver": p["solver"],
            "predicted_before_s": p["predicted_before_s"], "predicted_after_s": p["predicted_after_s"],
            "selected": p["selected"], "limits": p["limits"], "transition_actions": len(p["transition"]["actions"]),
            "peak_extra_usage": p["transition"]["peak_usage"], "final_extra_usage": applied["final_usage"],
            "apply_wall_s": data["managed_apply"]["wall_s"]}
        for row in data["restores"]:
            f = row["flow"]; plan = f["plan"]
            assert row["passed"] and row["verification"]["passed"]
            assert row["verification"]["checked_bytes"] == sum(t["n"] for t in plan["targets"])
            assert plan["placement_plan_id"] == p["plan_id"] and plan["strategy"] == "resource_lp" and not plan["fallback_reason"]
            window = loading(row)
            report["models"].append({**window, "predicted_transfer_s": plan["estimated_transfer_s"],
                "execute_wall_s": f["execute_wall_s"], "prediction_error_s": f["execute_wall_s"]-plan["estimated_transfer_s"],
                "verified_bytes": row["verification"]["checked_bytes"], "generation_checks": len(row["replies"]),
                "actual_strategy": plan["strategy"], "fallback_reason": plan["fallback_reason"], "source_bytes": f["source_bytes"]})
        assert len(report["models"]) == 1
    report["source_hash_mismatches"] = data.get("source_hash_mismatches")
    report["cleanup_errors"] = data.get("cleanup_errors")
    report["passed"] = data["status"] == "passed" and not report["source_hash_mismatches"] and not report["cleanup_errors"]
    (directory/"analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"passed": report["passed"], "fixture": report["fixture"],
        "placement": {k: report.get("placement", {}).get(k) for k in ("decision", "transition_actions", "final_extra_usage")},
        "models": [{k: m[k] for k in ("phase", "load_s", "execute_wall_s", "predicted_transfer_s", "verified_bytes")}
                   for m in report["models"]]}, ensure_ascii=False, indent=2))
    if not report["passed"]: raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--run-dir", type=Path, required=True)
    main(parser.parse_args().run_dir)
