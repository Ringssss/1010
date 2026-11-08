"""Build a complete evidence matrix offline without manufacturing observations."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import csv
import hashlib
import itertools
import json
from pathlib import Path
import re
import sys

from summarize_basic import clean_state, compare_protected, ordered_signature

ROOT = Path(__file__).resolve().parents[5]
RESULTS = Path("codex_coding/results/v0.1.23.3/task2")
BASIC = RESULTS / "basic_scenarios"
PAPER = RESULTS / "paper_e1_e8"
HISTORY = {
    "e1": "e1/e1_c02_qwen_ipc_r01", "e3": "e3/e3_c02_qwen_r01",
    "e4": "e4/e4_c01_qwen_r01", "e6": "e6/e6_c01_qwen_curve_r01",
}
COLUMNS = {
    "sglang_original": ("SGLang 原版加载/native", "original_system", "not_adapted"),
    "sglang_ipc_hip": ("HIP IPC 适配", "hip_adaptation", "implemented"),
    "janus_original": ("Janus 原版", "original_system", "not_adapted"),
    "janus_host": ("Janus warm 机制", "mechanism", "implemented"),
    "janus_d2d": ("Janus D2D 机制", "mechanism", "implemented"),
    "fastest": ("strong simple", "mechanism", "implemented"),
    "resource_lp": ("Fluid LP", "mechanism", "implemented"),
    "blitzscale_original": ("BlitzScale 原版", "original_system", "not_adapted"),
    "blitzscale_distribution": ("BlitzScale 适配", "mechanism", "not_adapted"),
    "serverlessllm_original": ("ServerlessLLM 原版", "original_system", "not_adapted"),
    "serverlessllm_locality": ("ServerlessLLM 适配", "mechanism", "not_adapted"),
}
NAMES = {"S1": "目标完整HBM", "S2": "CPU保活", "S3": "活跃复制",
         "S4": "零散空间", "S5": "物理共享资源"}
STATUS_ZH = {"new_measurement": "新实测", "historical_reference": "历史引用",
             "historical_alias": "历史别名", "alias": "别名", "unmeasured": "未测",
             "not_adapted": "未适配", "not_applicable": "不适用", "infeasible": "不可行",
             "missing": "缺输入", "failed": "失败", "observed_unaccepted": "未验收观察"}
STATUS_ZH["decision_only_alias"] = "决策别名/时延未测"
METRICS = {"T_ready_s": "s", "request_to_ready_ack_s": "s", "request_to_first_correct_token_s": "s",
           "prepare_s": "s", "execute_s": "s", "online_planning_s": "s", "post_ready_verification_s": "s"}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def identity_hash(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Inputs:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.manifest = {}
        self.missing = []

    def path(self, path):
        return Path(path) if Path(path).is_absolute() else self.root / path

    def label(self, path):
        path = self.path(path).resolve()
        return str(path.relative_to(self.root)) if path.is_relative_to(self.root) else str(path)

    def read(self, path, optional=False):
        path = self.path(path)
        if not path.is_file():
            if not optional:
                self.missing.append(self.label(path))
            return None
        before = sha(path)
        data = json.loads(path.read_text())
        if before != sha(path):
            raise RuntimeError(f"input changed during read: {path}")
        self.manifest[self.label(path)] = {"sha256": before, "bytes": path.stat().st_size}
        return data

    def verify(self):
        if any(sha(self.path(p)) != m["sha256"] for p, m in self.manifest.items()):
            raise RuntimeError("input changed during table generation")


def case_artifact(inputs, source, case):
    phase = case.get("phase")
    if not phase:
        return {}, None
    path = inputs.path(source).parent / f"{phase}_case.json"
    data = inputs.read(path, optional=True)
    return data or {}, inputs.label(path) if data is not None else None


def timing_proof(case):
    """Audit the primary clock window using embedded rank events, when present."""
    timing = case.get("timing") or {}
    ranks = timing.get("ranks") or {}
    if not ranks and len(timing.get("per_host", {})) == 1:
        ranks = next(iter(timing["per_host"].values())).get("ranks", {})
    starts, ends, hosts = [], [], set()
    for rank, detail in ranks.items():
        events = detail.get("events", [])
        begin = [e for e in events if e.get("event") == "load_begin"]
        end = [e for e in events if e.get("event") == "weights_ready"]
        if len(begin) != 1 or len(end) != 1:
            return {"same_host_clock_verified": False, "reason": "incomplete_rank_events"}
        starts.append(begin[0]["monotonic_ns"])
        ends.append(end[0]["monotonic_ns"])
        hosts.update([begin[0].get("host"), end[0].get("host")])
    if not ranks:
        return {"same_host_clock_verified": None, "reason": "no_embedded_rank_events"}
    primary = case.get("primary_T_ready_s", timing.get("primary_T_ready_s", timing.get("load_s")))
    measured = (max(ends) - min(starts)) / 1e9
    valid = len(hosts) == 1 and None not in hosts and primary is not None and abs(primary - measured) < 1e-9
    return {"same_host_clock_verified": valid, "hosts": sorted(str(x) for x in hosts),
            "rank_count": len(ranks), "recomputed_T_ready_s": measured}


def normalize_case(inputs, source, run, case, index, historical=False, scenario=None, partial=False):
    artifact, artifact_source = case_artifact(inputs, source, case)
    flow = case.get("flow") or {}
    plan = flow.get("plan") or {}
    ops = plan.get("ops", [])
    assessment = case.get("assessment") or case.get("pre_resume_admission") or {}
    ledger = assessment.get("resource_ledger", {})
    roles = {r["allocation_id"]: r["role"] for r in ledger.get("allocations", [])}
    method = case.get("method_id", case.get("routing", "sglang_ipc_hip" if scenario == "S1" else "unspecified"))
    sid = scenario or case.get("scenario_id", run.get("scenario_id", run.get("scene_id")))
    variant = case.get("scenario_variant", run.get("scenario_variant", run.get("variant", "default")))
    row_id = case.get("row_id")
    matrix_row = sid if sid != "S5" or variant == "default" and not row_id else "/".join(str(x) for x in (sid, variant, row_id) if x)
    primary = case.get("primary_T_ready_s", (case.get("timing") or {}).get("primary_T_ready_s", (case.get("timing") or {}).get("load_s")))
    logical = sum(o["n"] for o in ops) if ops else sum(e.get("nbytes", 0) for e in case.get("exports", [])) or None
    verification = case.get("verification") or flow.get("verification") or ((run.get("owner_setup") or {}).get("verification") if sid == "S1" else {}) or {}
    full_sha = verification.get("passed") is True and logical is not None and verification.get("checked_bytes") == logical
    clock = timing_proof(case)
    expected_ranks = len({o.get("tp") for o in ops}) if ops else len(case.get("exports", []))
    clock["expected_rank_count"] = expected_ranks or None
    clock["complete_rank_window"] = clock.get("same_host_clock_verified") is True and expected_ranks > 0 and clock.get("rank_count") == expected_ranks
    cleanup = clean_state(run) and not run.get("owned_remaining")
    accepted = run.get("status") == "passed" and case.get("status") == "passed" and case.get("correct") is True and cleanup and full_sha and clock["complete_rank_window"]
    applicability = assessment.get("status", "applicable" if primary is not None else "unknown")
    measured = primary is not None
    status = "historical_reference" if historical and measured else "new_measurement" if accepted and measured else "observed_unaccepted" if measured else "infeasible" if applicability == "infeasible" else "not_applicable" if applicability == "not_applicable" else "failed" if case.get("status") == "failed" else "unmeasured"
    budget = artifact.get("budget") or case.get("budget") or run.get("current_budget") or run.get("budget")
    if budget is None and ledger:
        budget = {key: ledger.get(key) for key in ("full_base_dram_bytes", "extra_cache_hbm_bytes", "ipc_owner_hbm_bytes", "existing_active_hbm_bytes", "incremental_active_hbm_bytes", "target_allocation_bytes", "staging_bytes", "extra_location_bytes")}
    if budget is None and historical:
        match = re.match(r"(\d+)gib", case.get("label", ""))
        budget = {"extra_hbm_bytes": int(match[1]) << 30 if match else None,
                  "scope": run.get("budget_scope"), "protocol": run.get("protocol")}
    workers = artifact.get("workers") or [w for m in run.get("memory_samples", []) for w in m.get("workers", [])]
    nodes = {w["worker"]: w.get("node") for w in workers if "worker" in w}
    destinations = sorted({o["dst_w"] for o in ops})
    target_nodes = sorted({nodes[w] for w in destinations if nodes.get(w)})
    hosts = clock.get("hosts", [])
    target = {"slot": case.get("target_slot", case.get("scenario", run.get("target_slot", run.get("_target_slot")))),
              "workers": destinations, "nodes": target_nodes, "hosts": hosts}
    tiers, source_workers = Counter(), Counter()
    for op in ops:
        parts = op.get("path", "").split(":")
        tiers[parts[1] if len(parts) > 1 else op.get("source_tier", "unknown")] += op["n"]
        source_workers[str(op["src_w"])] += op["n"]
    candidates = artifact.get("replicas")
    opportunity = identity_hash(sorted((r.get("id"), r.get("identity"), r.get("tp"), r.get("off"), r.get("n"), r.get("worker"), r.get("tier")) for r in candidates)) if candidates else run.get("session_sha256")
    format_contract = artifact.get("format_contract") or run.get("format_contract") or assessment.get("fairness", {}).get("format_contract")
    sid_source = run.get("run_id", inputs.path(source).parent.name)
    sample_id = f"{inputs.label(source)}#{case.get('phase', index)}"
    values = {"T_ready_s": primary, "request_to_ready_ack_s": case.get("request_to_ready_ack_s"),
              "request_to_first_correct_token_s": case.get("request_to_first_correct_token_s", case.get("request_to_first_token_s")),
              "prepare_s": flow.get("prepare_s"), "execute_s": flow.get("execute_wall_s"),
              "online_planning_s": case.get("online_planning_s", plan.get("planning_s")),
              "post_ready_verification_s": case.get("post_ready_verification_s")}
    return {"scenario_id": sid, "scenario_variant": variant, "matrix_row": matrix_row,
            "method_id": method, "identity": case.get("adaptation_identity", COLUMNS.get(method, (None, "unknown"))[1]),
            "run_id": sid_source, "source_run": inputs.label(inputs.path(source).parent), "source_file": inputs.label(source),
            "artifact_source": artifact_source, "phase": case.get("phase"), "label": case.get("label", row_id),
            "case_index": index,
            "sample_id": sample_id, "alias_of": None, "new_sample_count": int(not historical and measured),
            "independent_sample_count": int(measured), "accepted": accepted, "measurement_status": status,
            "applicability": applicability, "evidence_scope": "historical_partial_related" if partial else "exact_scenario",
            "target": target, "logical_bytes": logical, "budget": budget, "source_bytes_by_tier": dict(tiers),
            "selected_source_bytes": case.get("selected_source_bytes", dict(tiers)),
            "shared_cache_budget_bytes": case.get("shared_cache_budget_bytes"),
            "cache_budget_scope": (run.get("protocol") or {}).get("janus_host_budget_scope"),
            "control_type": case.get("control_type"),
            "cache_layout": run.get("planned_cache_layout"),
            "source_bytes_by_worker": dict(source_workers), "format_contract": format_contract,
            "legal_opportunity_sha256": opportunity, "executor": artifact.get("executor", flow.get("executor", "serial")),
            "comparison_group": case.get("comparison_group", run.get("comparison_group", inputs.label(source))),
            "decision_sha256": identity_hash(ordered_signature(ops, roles)) if ops else None,
            "operation_content_sha256": identity_hash(sorted(canonical(op) for op in ordered_signature(ops, roles))) if ops else None,
            "proof": {"full_sha_passed": full_sha, "checked_bytes": verification.get("checked_bytes"),
                      "verification_scope": "restore_copy_post_ready" if ops else "IPC_owner_establishment",
                      "owner_integrity_sha_passed": full_sha if not ops else None,
                      "per_hit_rehash": False if not ops else None,
                      "text_correct": case.get("correct"), "cleanup_complete": cleanup, "clock": clock,
                      "status": case.get("status"), "admission_reasons": assessment.get("reasons"),
                      "physical_resource_gate": case.get("physical_resource_gate", assessment.get("physical_resource_gate")),
                      "software_runtime_order_verification": case.get("software_runtime_order_verification"),
                      "software_gate": run.get("software_gate"),
                      "source_hash_mismatches": run.get("source_hash_mismatches", []),
                      "retained_base_unchanged": run.get("retained_base_unchanged", run.get("cpu_sources_unchanged")),
                      "protected_catalog_unchanged": run.get("protected_catalog_unchanged"),
            "verification_source": "case.verification/flow.verification" if ops else "IPC owner_setup.verification plus hit correctness and rank events"},
            "metrics": values}


def aliases_for(observations, run):
    by_phase = {x["phase"]: x for x in observations}
    aliases = []
    for index, alias in enumerate(run.get("route_aliases", [])):
        source = by_phase.get(alias.get("source_phase"))
        if source is None:
            raise ValueError("alias refers to missing independent source phase")
        row = copy.deepcopy(source)
        row.update(method_id=alias.get("method_id", alias.get("routing", source["method_id"])),
                   alias_of=source["sample_id"], sample_id=source["source_file"] + f"#alias{index}",
                   label=alias.get("label"), new_sample_count=0, independent_sample_count=0,
                   measurement_status="historical_alias" if source["measurement_status"] == "historical_reference" else "alias")
        row["proof"]["alias_reason"] = alias.get("reason")
        aliases.append(row)
    return aliases


def cell_status(scope, observations, missing=False):
    statuses = {o["measurement_status"] for o in observations}
    for status in ("new_measurement", "observed_unaccepted", "failed", "decision_only_alias", "historical_reference", "historical_alias", "alias", "infeasible", "not_applicable"):
        if status in statuses:
            return status
    if missing:
        return "missing"
    applicable = scope.get("applicability")
    if applicable in ("not_applicable", "infeasible"):
        if not scope.get("reason") or not (scope.get("source_files") or scope.get("proof")):
            return "unmeasured"
        return applicable
    if applicable == "not_adapted" or scope.get("implementation") in ("not_adapted", "pending", "unsupported"):
        return "not_adapted"
    return "unmeasured"


def comparable(a, b):
    """Require known identical experimental conditions before deriving differences."""
    keys = ("matrix_row", "target", "logical_bytes", "budget", "format_contract", "executor", "legal_opportunity_sha256", "comparison_group")
    if a.get("alias_of") or b.get("alias_of") or not a.get("accepted") or not b.get("accepted"):
        return False
    if a.get("measurement_status") != "new_measurement" or b.get("measurement_status") != "new_measurement":
        return False
    return all(a.get(k) not in (None, {}, []) and canonical(a[k]) == canonical(b.get(k)) for k in keys)


def comparisons(observations):
    result = []
    for a, b in itertools.combinations(observations, 2):
        if a["method_id"] == b["method_id"] or not comparable(a, b):
            continue
        same = a["decision_sha256"] is not None and a["decision_sha256"] == b["decision_sha256"]
        result.append({"a": a["sample_id"], "b": b["sample_id"], "matrix_row": a["matrix_row"],
                       "observed_T_ready_difference_s": a["metrics"]["T_ready_s"] - b["metrics"]["T_ready_s"],
                       "same_ordered_decision": same, "algorithm_gain": False,
                       "conclusion": "same_ordered_decision_not_algorithm_gain" if same else "single_configuration_observation_not_algorithm_gain"})
    return result


def control_comparisons(observations):
    """Compare execution controls only after matching contents and resource scope."""
    result = []
    for a, b in itertools.combinations(observations, 2):
        if any(x.get("control_type") != "execution_order" or x.get("scenario_id") != "S5" for x in (a, b)):
            continue
        if a["method_id"] != b["method_id"] or a["matrix_row"] == b["matrix_row"]:
            continue
        if a["matrix_row"].endswith("rank_interleaved") and b["matrix_row"].endswith("target_major"):
            a, b = b, a
        if not a["matrix_row"].endswith("target_major") or not b["matrix_row"].endswith("rank_interleaved"):
            continue
        left, right = copy.copy(a), copy.copy(b)
        left["matrix_row"] = right["matrix_row"] = "S5/" + a["scenario_variant"]
        if a["scenario_variant"] != b["scenario_variant"] or not comparable(left, right):
            continue
        keys = ("cache_layout", "selected_source_bytes", "source_bytes_by_worker", "operation_content_sha256")
        if not all(a.get(k) not in (None, {}, []) and canonical(a[k]) == canonical(b.get(k)) for k in keys):
            continue
        if not all((x["proof"].get("software_runtime_order_verification") or {}).get("passed") is True
                   and x["proof"].get("physical_resource_gate") == "not_measured" for x in (a, b)):
            continue
        result.append({"run_id": a["run_id"], "a": a["sample_id"], "b": b["sample_id"],
                       "a_control": a["matrix_row"], "b_control": b["matrix_row"],
                       "observed_order": [a["label"], b["label"]] if a["case_index"] < b["case_index"] else [b["label"], a["label"]],
                       "a_T_ready_s": a["metrics"]["T_ready_s"], "b_T_ready_s": b["metrics"]["T_ready_s"],
                       "observed_T_ready_difference_s": a["metrics"]["T_ready_s"] - b["metrics"]["T_ready_s"],
                       "reference_control": "target_major", "candidate_control": "rank_interleaved",
                       "observed_saved_s": a["metrics"]["T_ready_s"] - b["metrics"]["T_ready_s"],
                       "observed_saved_percent": (a["metrics"]["T_ready_s"] - b["metrics"]["T_ready_s"]) / a["metrics"]["T_ready_s"] * 100,
                       "matched_scope": list(keys) + ["target", "budget", "format_contract", "executor", "legal_opportunity_sha256"],
                       "algorithm_gain": False, "physical_resource_gate": "not_measured",
                       "conclusion": "software_execution_order_single_run_observation"})
    return result


def make_matrix(observations, scopes):
    scope_by = {(x["scenario_id"], x["method_id"]): x for x in scopes}
    row_ids = list(NAMES) + sorted({x["matrix_row"] for x in observations} - set(NAMES))
    matrix = []
    for row_id in row_ids:
        sid = row_id.split("/")[0]
        cells = {}
        for method, (name, identity, implementation) in COLUMNS.items():
            rows = [o for o in observations if o["matrix_row"] == row_id and o["method_id"] == method]
            scope = scope_by.get((sid, method), {"implementation": implementation, "applicability": "unknown", "reason": "缺少本场景范围证据"})
            independent = {o["sample_id"] for o in rows if o["independent_sample_count"]}
            new = {o["sample_id"] for o in rows if o["new_sample_count"]}
            accepted_new = {o["sample_id"] for o in rows if o["new_sample_count"] and o["accepted"]}
            status = cell_status(scope, rows)
            if row_id == "S5":
                status = "unmeasured"
            cells[method] = {"status": status, "identity": identity,
                             "applicability": scope.get("applicability"), "scope_evidence": scope,
                             "independent_samples": len(independent), "new_samples": len(new), "accepted_new_samples": len(accepted_new),
                             "aliases": sum(bool(o["alias_of"]) for o in rows), "observation_ids": [o["sample_id"] for o in rows],
                             "T_ready": [{"sample_id": o["sample_id"], "value_s": o["metrics"]["T_ready_s"], "target": o["target"], "label": o["label"], "alias_of": o["alias_of"], "evidence_scope": o["evidence_scope"], "measurement_status": o["measurement_status"]} for o in rows if o["metrics"].get("T_ready_s") is not None]}
        matrix.append({"row_id": row_id, "scenario_id": sid, "name": NAMES.get(sid), "cells": cells})
    return matrix


def long_measurements(observations):
    rows = []
    for observation in observations:
        common = {k: v for k, v in observation.items() if k != "metrics"}
        present = {k: v for k, v in observation["metrics"].items() if v is not None}
        for metric, value in (present or {"measurement_status": None}).items():
            rows.append(dict(common, metric=metric, value=value, unit=METRICS.get(metric)))
    return rows


def audit_fastest(root, runs, method="fastest"):
    """Recompute saved-case decisions only; no endpoint is contacted."""
    sys.path.insert(0, str(Path(root) / "main_lib"))
    from fluidckpt.benchmarks.basic_scenarios import build_method_plan
    inputs = Inputs(root)
    audits = []
    for source in runs:
        source = Path(source)
        if source.suffix != ".json":
            source /= "summary.json"
        run = inputs.read(source)
        allowed_scenarios = ("S2", "S3") if method == "fastest" else ("S5",)
        if not run or run.get("scenario_id", run.get("scene_id")) not in allowed_scenarios:
            continue
        for index, case in enumerate(run.get("cases", [])):
            if method == "resource_lp" and not case.get("row_id", "").endswith("target_major"):
                continue
            artifact, artifact_source = case_artifact(inputs, source, case)
            if not artifact:
                continue
            decision = build_method_plan(artifact, method, Path(root) / "lib_cite/baselines.lock.json")
            planned = decision.get("plan") or {}
            source_plan = (case.get("flow") or {}).get("plan") or {}
            roles = {r["id"]: r.get("source_role", "unknown") for r in artifact.get("replicas", [])}
            actual_signature = ordered_signature(source_plan.get("ops", []), roles)
            candidate_signature = ordered_signature(planned.get("ops", []), roles)
            same = bool(actual_signature) and decision["status"] == "applicable" and actual_signature == candidate_signature
            audits.append({"scenario_id": run.get("scenario_id", run.get("scene_id")), "source_file": inputs.label(source),
                           "source_phase": case["phase"], "source_method": case["method_id"], "artifact_source": artifact_source,
                           "method_id": method, "status": decision["status"], "reasons": decision.get("reasons"),
                           "requested_scene_id": artifact.get("requested_scene_id", run.get("scenario_id")),
                           "eligibility_contract": artifact.get("scenario_id"),
                           "physical_resource_gate": run.get("physical_resource_gate"),
                           "same_ordered_decision": same, "source_decision_sha256": identity_hash(actual_signature),
                           "decision_sha256": identity_hash(candidate_signature) if candidate_signature else None,
                           "source_ids": decision.get("source_ids"), "source_bytes_by_tier": dict(Counter({tier: sum(o["n"] for o in planned.get("ops", []) if o.get("path", "").split(":")[1:2] == [tier]) for tier in ("dram", "hbm")})),
                           "measurement_status": "decision_only_alias" if same else "decision_only_different" if decision["status"] == "applicable" else "decision_only_unavailable",
                           "runtime_opportunity": "historical_saved_case_only_not_live", "new_gpu_samples": 0,
                           "T_ready_s": None, "algorithm_gain": False})
    inputs.verify()
    return {"status": "passed", "audit_kind": "offline_decision_only", "new_gpu_samples": 0, "audits": audits, "missing_inputs": inputs.missing}, inputs.manifest


def build(root=ROOT, runs=(), scope_file=None, include_history=True, decision_audit=None, supporting_sources=()):
    inputs = Inputs(root)
    observations, scope_rows, algorithm_adaptations, historical_software = [], [], [], []
    audit = inputs.read(BASIC / "evidence_r01/summary.json") if include_history else None
    if scope_file:
        scope = inputs.read(scope_file)
        if scope:
            scope_rows = scope.get("baseline_scopes", [])
            algorithm_adaptations = scope.get("algorithm_adaptations", [])
    if include_history:
        for key, rel in HISTORY.items():
            source = PAPER / rel / "summary.json"
            run = inputs.read(source)
            if not run:
                continue
            batch = []
            for index, case in enumerate(run.get("hits", []) if key == "e1" else run.get("cases", [])):
                sid = "S1" if key == "e1" else "S2" if key == "e6" and case.get("label", "").startswith("0gib") else "S4"
                batch.append(normalize_case(inputs, source, run, case, index, True, sid, key != "e1"))
            observations.extend(batch + aliases_for(batch, run))
            if key == "e3":
                for row in batch:
                    related = copy.deepcopy(row)
                    related.update(evidence_scope="historical_software_dependency_only", new_sample_count=0)
                    historical_software.append(related)
    for source in runs:
        source = Path(source)
        if source.suffix != ".json":
            source /= "summary.json"
        run = inputs.read(source)
        if run is None:
            continue
        sid = run.get("scenario_id", run.get("scene_id", "S1" if "hits" in run else None))
        if sid not in NAMES:
            raise ValueError(f"not a scenario run: {source}")
        batch = [normalize_case(inputs, source, run, c, i, scenario=sid) for i, c in enumerate(run.get("hits", []) if sid == "S1" else run.get("cases", []))]
        observations.extend(batch + aliases_for(batch, run))
    for audit_path in (decision_audit if isinstance(decision_audit, (list, tuple)) else [decision_audit] if decision_audit else []):
        decision_data = inputs.read(audit_path) or {}
        for audit_row in decision_data.get("audits", []):
            if audit_row.get("measurement_status") != "decision_only_alias":
                continue
            source = next((o for o in observations if o["source_file"] == audit_row["source_file"] and o["phase"] == audit_row["source_phase"]), None)
            alias_method = audit_row.get("method_id", "fastest")
            if source is None or source["method_id"] == alias_method:
                continue
            if any(o["method_id"] == alias_method and o["matrix_row"] == source["matrix_row"] and o.get("alias_of") == source["sample_id"] for o in observations):
                continue
            row = copy.deepcopy(source)
            row.update(method_id=alias_method, identity="mechanism", alias_of=source["sample_id"],
                       sample_id=inputs.label(audit_path) + "#" + source["phase"],
                       new_sample_count=0, independent_sample_count=0, accepted=False,
                       measurement_status="decision_only_alias", metrics={k: None for k in METRICS})
            row["proof"]["offline_decision_audit"] = audit_row
            observations.append(row)
    supporting = []
    protected_after = None
    for source in supporting_sources:
        path = Path(source)
        if path.suffix != ".json":
            path /= "summary.json"
        data = inputs.read(path)
        if data is None:
            continue
        probes = data.get("endpoint_probes", [])
        supporting.append({"source_file": inputs.label(path), "status": data.get("status"),
                           "evidence_kind": "platform_check" if probes else "independent_review",
                           "measurement_status": data.get("measurement_status"), "read_only": data.get("read_only"),
                           "live_workers": [x["worker"] for x in probes if x["status"] == "passed"],
                           "unavailable_workers": [x["worker"] for x in probes if x["status"] != "passed"],
                           "retained_B": data.get("retained_B"), "new_samples": 0})
        if len(probes) == 22:
            protected_after = data
    protected_check = None
    if protected_after:
        protected_before = inputs.read(BASIC / "runtime_preflight_r02/summary.json")
        if protected_before:
            protected_check = compare_protected(protected_before, protected_after)
    unique = {x["sample_id"]: x for x in observations if x["independent_sample_count"]}
    summary = {"schema_version": 1, "development": "v0.1.23.3/task2", "audit_kind": "offline_complete_table",
               "status": "complete_matrix_with_gaps", "generated_gpu_samples": 0,
               "columns": [{"method_id": k, "name": v[0], "identity": v[1]} for k, v in COLUMNS.items()],
               "matrix": make_matrix(observations, scope_rows), "observations": observations,
               "measurements": long_measurements(observations), "comparisons": comparisons(observations),
               "control_comparisons": control_comparisons(observations),
               "algorithm_adaptations": algorithm_adaptations,
               "historical_software_evidence": historical_software,
               "original_system_status": [x for x in scope_rows if x.get("identity") == "original_system"],
               "supporting_evidence": supporting, "protected_metadata_check": protected_check,
               "counts": {"unique_independent_historical_observations": sum(x["measurement_status"] == "historical_reference" for x in unique.values()),
                          "unique_new_observations": sum(x["new_sample_count"] for x in unique.values()),
                          "unique_accepted_new_observations": sum(x["new_sample_count"] and x["accepted"] for x in unique.values()),
                          "aliases": len({x["sample_id"] for x in observations if x["alias_of"]})},
               "historical_audit": {"source": str(BASIC / "evidence_r01/summary.json"), "hashes_unchanged_at_audit": (audit or {}).get("historical_hashes_unchanged"), "new_samples": 0},
               "missing_inputs": inputs.missing,
               "rules": ["历史引用与别名均不增加新样本；一个原始观测映射多个场景仅计一次。", "原版、硬件适配与共同执行器机制保持分列。", "不同目标、预算、格式或合法来源不计算比较；同操作不宣称算法增益。", "S5软件串行控制不是物理出口争用实验。", "新实测数来自输入run；此CPU汇总没有新增GPU样本。"]}
    inputs.verify()
    return summary, inputs.manifest


def cell_label(cell):
    state = cell["status"]
    if state == "new_measurement":
        vals = [x for x in cell["T_ready"] if not x["alias_of"] and x["measurement_status"] == "new_measurement"]
        return "新 " + ", ".join(f"{x['value_s']:.6f}s" for x in vals) + f" (n={cell['accepted_new_samples']})"
    if state == "historical_reference":
        partial = any(x["evidence_scope"] != "exact_scenario" for x in cell["T_ready"])
        return f"历史{'相关' if partial else ''} n={cell['independent_samples']}" + (f" +{cell['aliases']}别名" if cell["aliases"] else "")
    if state in ("historical_alias", "alias"):
        return f"{STATUS_ZH[state]} {cell['aliases']} (n=0)"
    if state == "unmeasured" and cell["independent_samples"]:
        return f"未测；历史软件相关 n={cell['independent_samples']}"
    return STATUS_ZH[state]


def row_label(row):
    if row["row_id"] == "S5":
        return "S5-P 物理共享"
    if row["scenario_id"] == "S5":
        return "S5-S " + row["row_id"].rsplit("/", 1)[-1].removeprefix("fastest_")
    return row["row_id"]


def render(summary):
    lines = ["# S1–S5 × baseline 完整证据表", "", "开发编号：v0.1.23.3/task2。单元格表示当前证据状态；时延为目标同主机权重可用窗口。小表不混合目标或预算求均值。", "",
             "新=输入run的独立新实测；历史=保留原run身份的引用；历史相关=场景仅部分覆盖；别名不计独立样本。未适配、未测、不适用、明确不可行和缺输入分开记录。", "",
             "| 场景 | " + " | ".join(x["name"] for x in summary["columns"]) + " |",
             "| --- | " + " | ".join("---" for _ in summary["columns"]) + " |"]
    for row in summary["matrix"]:
        lines.append("| " + row_label(row) + " | " + " | ".join(cell_label(row["cells"][c["method_id"]]) for c in summary["columns"]) + " |")
    lines.extend(["", "详细数值、run/target/预算/来源/完整性证明与别名见 measurements.csv 和 summary.json。", "",
                  "| 场景/变体 | 方法 | run / 原标签 | 目标 | T_ready(s) | 独立/新增 | 证据 |", "| --- | --- | --- | --- | ---: | --- | --- |"])
    for row in summary["observations"]:
        if row["alias_of"] or row["metrics"]["T_ready_s"] is None:
            continue
        slot = row["target"]["slot"]
        target = slot if isinstance(slot, str) else ",".join(row["target"]["nodes"] or row["target"]["hosts"]) or "unknown"
        lines.append(f"| {row['matrix_row']} | {row['method_id']} | {row['run_id']} / {row['label'] or row['phase']} | {target} | {row['metrics']['T_ready_s']:.9f} | {row['independent_sample_count']}/{row['new_sample_count']} | {STATUS_ZH[row['measurement_status']]} |")
    lines.extend(["", "SGLang冷加载/native未完成统一权重窗口测量，未标为不可行；Janus warm/D2D为共同执行器机制。历史强简单/Fluid别名保留原决策与样本来源。S3源建立IPC接入不是独立S1样本；bootstrap不是baseline性能样本。", "",
                  "S1两个IPC接管共享已全量SHA验证的完整HBM owner，每次接管均独立验证同机rank窗口、文本与detach；不会把owner建立阶段的完整SHA说成每次IPC hit重新哈希。其余copy恢复均保留该次完整传输SHA证明。", "",
                  "S4若使用共同8GiB缓存fixture，Janus host的实际取数仍可全部来自DRAM；共享fixture预算用于同条件时延对照，不作为Janus单独方案的最低HBM保活空间。详细表保留实际来源字节与共享缓存预算。", ""])
    for comparison in summary["comparisons"]:
        lines.append(f"{comparison['matrix_row']}：{comparison['conclusion']}；单轮T_ready观察差值{comparison['observed_T_ready_difference_s']:.9f}s。")
    for control in summary["control_comparisons"]:
        lines.extend(["", f"{control['run_id']} 软件执行控制：实际顺序{' → '.join(control['observed_order'])}；reference target_major={control['a_T_ready_s']:.9f}s，candidate rank_interleaved={control['b_T_ready_s']:.9f}s，观察降低{control['observed_saved_s']:.9f}s（{control['observed_saved_percent']:.2f}%）。来源、字节、布局、目标与预算匹配；algorithm_gain=false，物理共享资源仍未测。"])
    if summary.get("historical_software_evidence"):
        lines.extend(["", "历史e3的4个原始布局观察提供worker串行依赖相关证据，单独保存在historical_software_evidence；不向S5-P物理共享行映射，也不增加样本。"])
    lines.extend(["", "原版SGLang、Janus、BlitzScale、ServerlessLLM均未完成本平台baseline运行适配/测量；源码获取与这些机制结果不替代原版复现。", ""])
    for adaptation in summary.get("algorithm_adaptations", []):
        lines.append(f"{adaptation['algorithm_id']}：{adaptation['scope_level']}，{adaptation['scope']}；executable_restore_plan={str(adaptation.get('executable_restore_plan')).lower()}，没有端到端恢复性能样本。")
    protected = summary.get("protected_metadata_check")
    if protected:
        lines.extend(["", f"最终保护检查：原可访问{len(protected['before_live_workers'])}个keeper的VALID目录与epoch/session元数据保持一致={protected['metadata_unchanged']}；原不可访问集合保持一致={protected['unavailable_set_unchanged']}。旧node27的8个拒绝连接端点延续既有失效，未当作本轮损坏；此处没有重新哈希旧内容。"])
    for support in summary.get("supporting_evidence", []):
        if support["evidence_kind"] == "independent_review":
            lines.append(f"独立审查{Path(support['source_file']).parent.name}：status={support['status']}；复用已有观察，不计性能样本。")
        else:
            lines.append(f"平台检查{Path(support['source_file']).parent.name}：status={support['status']}，可访问workers={support['live_workers']}，不可访问={support['unavailable_workers']}；不计性能样本。")
    lines.extend(["", "；".join(summary["rules"]), "", f"唯一历史独立观察={summary['counts']['unique_independent_historical_observations']}，输入新观察={summary['counts']['unique_new_observations']}，新观察验收通过={summary['counts']['unique_accepted_new_observations']}；本汇总新增GPU样本=0。", ""])
    if summary["missing_inputs"]:
        lines.extend(["缺输入：" + "；".join(summary["missing_inputs"]), ""])
    return "\n".join(lines)


def write_outputs(summary, manifest, out):
    out = Path(out)
    if out.exists():
        raise FileExistsError("output run already exists; choose a new run ID")
    out.mkdir(parents=True)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    (out / "inputs_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (out / "report.md").write_text(render(summary))
    rows = summary["measurements"]
    with (out / "measurements.csv").open("w", newline="") as stream:
        fields = list(rows[0]) if rows else ["scenario_id", "method_id", "metric", "value"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: canonical(v) if isinstance(v, (dict, list)) else v for k, v in row.items()})
    with (out / "table.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["scenario"] + list(COLUMNS))
        for row in summary["matrix"]:
            writer.writerow([row_label(row)] + [cell_label(row["cells"][method]) for method in COLUMNS])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--run", type=Path, action="append")
    parser.add_argument("--scope", type=Path)
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--decision-audit", type=Path, action="append")
    parser.add_argument("--decision-audit-only", action="store_true")
    parser.add_argument("--decision-method", choices=("fastest", "resource_lp"), default="fastest")
    parser.add_argument("--support", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    allowed = (args.repo_root / BASIC).resolve()
    if not out.is_relative_to(allowed) or out == allowed:
        raise ValueError("output must be a new run within basic_scenarios")
    runs = args.run if args.run is not None else [BASIC / "s2_r01", BASIC / "s3_r01"]
    if args.decision_audit_only:
        summary, manifest = audit_fastest(args.repo_root, runs, args.decision_method)
        if out.exists():
            raise FileExistsError("output run already exists")
        out.mkdir(parents=True)
        (out / "decision_audit.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        (out / "inputs_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(summary, ensure_ascii=False))
        return
    summary, manifest = build(args.repo_root, runs, args.scope, not args.no_history, args.decision_audit, args.support)
    write_outputs(summary, manifest, out)
    print(json.dumps({"out": str(out), "counts": summary["counts"], "rows": len(summary["matrix"]), "columns": len(COLUMNS), "missing_inputs": summary["missing_inputs"]}))


if __name__ == "__main__":
    main()
