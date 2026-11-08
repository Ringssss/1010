"""Audit native CPU evidence and independently exercise admission/cleanup failures."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[6]
ADAPTER = ROOT / "codex_coding/src/v0.1.23.3/task2/multitier_scenarios/adapter/adapter.py"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--native", type=Path, required=True)
    ap.add_argument("--journal", type=Path, required=True)
    ap.add_argument("--native-adapter-snapshot", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    checks, rejects, refs = {}, {}, []
    spec = importlib.util.spec_from_file_location("reviewed_adapter", ADAPTER)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    from fluidckpt.cluster.catalog import Catalog
    contract = adapter.load_contract()
    nq = json.loads((args.native / "qualification.json").read_text())
    jq = json.loads((args.journal / "qualification.json").read_text())
    runs = json.loads((args.native / "native_api_runs.json").read_text())
    checks["native_CPU_qualification_all_passed"] = nq["passed"] and all(x["passed"] for x in nq["checks"])
    checks["journal_metadata_qualification_all_passed"] = jq["passed"] and all(x["passed"] for x in jq["checks"])
    native_source = args.native_adapter_snapshot or ADAPTER
    checks["native_recorded_adapter_SHA_exact"] = nq["source_sha256"][str(ADAPTER)] == sha(native_source)
    final_source = ADAPTER.read_text()
    reversed_source = final_source.replace("        _require(current.get('budget_bytes') == expected.get('budget_bytes'), 'worker catalog budgets changed')\n", "")
    reversed_source = reversed_source.replace("    if result.get('workers'):\n        check_current_workers(after_workers, dict(workers=result['workers']))\n", "")
    checks["final_delta_only_budget_and_postcleanup_identity_assertions"] = native_source.read_text() == final_source or native_source.read_text() == reversed_source
    checks["native_no_GPU_remote_or_rates"] = nq["GPU_calls"] == 0 and nq["remote_operations"] == 0 and nq["rate_samples"] == 0
    checks["native_scope_labels_CPU_not_physical"] = nq["physical_ready"] is False and nq["model_ready_measured"] is False and len(nq["CPU_mock_components"]) == 4
    checks["native_six_recipes_exact"] = {x["phase"] for x in runs} == {"C0", "C1", "C2a", "C2b", "C3", "SSD_only"}
    for run in runs:
        result = run["restore_result"]
        verification = result["verification"]
        checks[run["phase"] + "/32MiB_SHA_every_operation"] = verification["passed"] and verification["checked_bytes"] == 32 << 20 and len(verification["operations"]) == 8 and all(
            r["source_sha256"] == r["target_sha256"] and r["n"] == 4 << 20 for r in verification["operations"].values())
        checks[run["phase"] + "/four_full_target_ranges"] = len(run["restore_plan"]["targets"]) == 4 and all(t["n"] == 8 << 20 and t["off"] == 0 for t in run["restore_plan"]["targets"])
        checks[run["phase"] + "/serial_actual_native_plan"] = run["restore_plan"]["executor"] == "serial"
    binding = json.loads((args.native / "binding_cpu_fixture.json").read_text())
    inventory = json.loads((args.native / "current_inventory_cpu_fixture.json").read_text())
    # Use current frozen source as explicit CPU fixture evidence, never physical evidence.
    snapshot = args.out / "adapter.source.py"
    snapshot.write_bytes(ADAPTER.read_bytes())
    fixture_ref = {"path": str(snapshot.resolve()), "sha256": sha(snapshot)}
    binding["evidence_refs"] = {k: fixture_ref for k in binding["evidence_refs"]}
    binding["valid_until"] = time.time() + 600
    for item in list(binding["paths"].values()) + list(binding["phase_admission"].values()):
        item["valid_until"] = time.time() + 600
    for admission in binding["phase_admission"].values():
        admission["budget_accounting_complete"] = True
    rows = inventory["replicas"]
    def reject(name, action):
        try:
            action()
        except (ValueError, MemoryError) as exc:
            rejects[name] = str(exc)
            checks[name] = True
        else:
            checks[name] = False
    reject("negative/unbound_compile", lambda: adapter.compile_phase(contract, None, "C0", "independent", rows))
    reject("negative/CPU_fixture_execute", lambda: adapter.execute_phase(None, contract, binding, "C0", "independent"))
    physical_lie = copy.deepcopy(binding)
    physical_lie["purpose"] = "physical"
    reject("negative/relabel_fixture_no_trusted_gate", lambda: adapter.execute_phase(None, contract, physical_lie, "C0", "independent"))
    bad = copy.deepcopy(binding)
    bad["workers"][1]["gpu"] = bad["workers"][0]["gpu"]
    reject("negative/duplicate_GPU", lambda: adapter.check_binding(bad, contract))
    bad = copy.deepcopy(binding)
    bad["valid_until"] = 0
    reject("negative/stale_binding", lambda: adapter.check_binding(bad, contract))
    bad = copy.deepcopy(binding)
    next(iter(bad["evidence_refs"].values()))["sha256"] = "0" * 64
    reject("negative/tampered_evidence_SHA", lambda: adapter.check_binding(bad, contract))
    bad = copy.deepcopy(binding)
    bad["paths"] = {}
    reject("negative/unqualified_selected_path", lambda: adapter.compile_phase(contract, bad, "C0", "independent", rows))
    bad = copy.deepcopy(binding)
    bad["phase_admission"]["C0"]["budget_accounting_complete"] = False
    reject("negative/incomplete_budget_ledger", lambda: adapter.compile_phase(contract, bad, "C0", "independent", rows))
    bad_workers = copy.deepcopy(binding["workers"])
    bad_workers[0]["budget_bytes"]["hbm"] += 4096
    reject("negative/current_worker_budget_drift", lambda: adapter.check_current_workers(bad_workers, binding))
    reject("negative/missing_authoritative_source", lambda: adapter.compile_phase(contract, binding, "C0", "independent", rows[1:]))
    expected = copy.deepcopy(rows[0])
    expected["generation"] += 1
    reject("negative/base_generation_drift", lambda: adapter.bind_source(expected, rows, binding["workers"]))

    class MetadataCluster:
        executor = "serial"
        def __init__(self):
            self.endpoints = [w["endpoint"] for w in physical_lie["workers"]]
            self.catalogs = [Catalog(w["budget_bytes"]) for w in physical_lie["workers"]]
            for row in rows:
                catalog = self.catalogs[row["worker"]]
                catalog.reserve(row, lambda: (None, None))
                catalog.publish(row["id"], True)
        def inventory(self):
            workers = copy.deepcopy(physical_lie["workers"])
            for w in workers:
                w["transactions"] = list(self.catalogs[w["worker"]].transactions)
            return workers, [r for c in self.catalogs for r in c.inventory()]
        def transfer(self, plan, verify_before_ready=True):
            targets = plan["targets"] if plan["adapter_stage"] == "save" else plan["targets"][:1]
            for target in targets:
                worker = physical_lie["workers"][target["worker"]]
                desc = dict(target, node=worker["node"], gpu=worker["gpu"], epoch=worker["epoch"])
                c = self.catalogs[target["worker"]]
                c.reserve(desc, lambda: (None, None))
                c.publish(target["id"], True)
            if plan["adapter_stage"] == "restore":
                raise RuntimeError("independent_partial_restore_failure")
            return {"scope": "CPU metadata only", "verification": {"passed": True}}
        def retire(self, rid):
            for c in self.catalogs:
                if rid in c.entries:
                    c.retire(rid)
                    return
            raise ValueError("unknown exact owned ID")
    cluster = MetadataCluster()
    def explicit_CPU_mock_gate(_binding, phase, run_id):
        return {"status": "admitted", "contract_hash": adapter.FROZEN, "phase": phase, "run_id": run_id,
                "scope": "explicit CPU metadata mock coordinator; no physical evidence"}
    try:
        adapter.execute_phase(cluster, contract, physical_lie, "C0", "independent", trusted_admission=explicit_CPU_mock_gate,
                              journal_path=args.out / "injected_failure_journal.json")
    except adapter.AdapterExecutionError as exc:
        journal = exc.journal
        checks["failure/all_12_IDS_recorded_before_failure"] = len(journal["owned_ids"]) == 12
        bad = copy.deepcopy(journal)
        bad["owned_ids"].append(bad["base_ids"][0])
        reject("negative/base_in_cleanup_list", lambda: adapter.cleanup_owned(cluster, bad))
        bad = copy.deepcopy(journal)
        bad["base_descriptors"][0]["generation"] += 1
        reject("negative/cleanup_changed_base_generation", lambda: adapter.cleanup_owned(cluster, bad))
        result = adapter.cleanup_owned(cluster, journal)
        checks["failure/exact_9_retired_3_absent"] = len(result["retired"]) == 9 and len(result["absent_or_released"]) == 3
        checks["failure/base_eight_VALID_unleased_preserved"] = len(result["preserved_base_descriptors"]) == 8 and all(r["state"] == "VALID" and r["leases"] == 0 for r in result["preserved_base_descriptors"])
        (args.out / "injected_failure_cleanup.json").write_text(json.dumps(result, indent=2) + "\n")
    else:
        checks["failure/injected_failure_caught"] = False
    for directory in (args.native, args.journal):
        for path in sorted(directory.glob("*.json")):
            refs.append({"path": str(path.resolve()), "sha256": sha(path)})
    result = {"qualification": "independent_CPU_API_and_failure_controls_passed" if all(checks.values()) else "failed",
              "passed": all(checks.values()), "checks": checks, "check_count": len(checks), "negative_rejection_reasons": rejects,
              "physical_execution_admitted": False, "physical_trusted_admission_implementation_available": False,
              "trust_boundary": "binding is compile input; coordinator callback must independently audit real path semantics and per-node aggregate budget",
              "physical_ready": False, "GPU_calls": 0, "SSH_calls": 0, "evidence_refs": refs,
              "adapter_sha256": sha(ADAPTER)}
    (args.out / "qualification.json").write_text(json.dumps(result, indent=2) + "\n")
    (args.out / "audit.source.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"passed": result["passed"], "checks": len(checks), "out": str(args.out)}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
