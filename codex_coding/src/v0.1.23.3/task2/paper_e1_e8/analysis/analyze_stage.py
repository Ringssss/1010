"""Read immutable e1/e3-e6 runs and write a separate, qualified evidence report."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import time


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def passed(rows):
    return bool(rows) and all(value is True for value in rows)


def delta(baseline, candidate):
    if not number(baseline) or not number(candidate):
        return {"baseline_s": baseline, "candidate_s": candidate, "saved_s": None, "improvement_pct": None}
    return {"baseline_s": baseline, "candidate_s": candidate, "saved_s": baseline - candidate,
            "improvement_pct": 100 * (baseline - candidate) / baseline if baseline > 0 else None}


def layout_signature(selected):
    """Ignore names and candidate segmentation while preserving rank/location."""
    grouped = {}
    for row in selected:
        grouped.setdefault((row.get("identity", ""), row["tp"], row["worker"], row["tier"]), []).append((row["off"], row["off"] + row["n"]))
    answer = []
    for key, extents in sorted(grouped.items()):
        merged = []
        for begin, end in sorted(extents):
            if merged and begin <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((begin, end))
        answer.extend([*key, begin, end - begin] for begin, end in merged)
    return answer


def route_signature(plan):
    fields = ("src_w", "dst_w", "identity", "tp", "off", "source_offset", "replica_id", "n", "kind", "path")
    return [[op.get(key) for key in fields] for op in plan["ops"]]


def alias_rows(path, data, measured):
    result, errors = [], []
    declared = list(data.get("route_aliases", []))
    declared_keys = {(r["label"], r["scenario"], r["routing"]) for r in declared + measured}
    # Older runs recorded whole-policy deduplication without explicit per-route aliases.
    for item in data.get("deduplicated", []):
        budget = item["budget_gib"] << 30
        try:
            solution = load(path / f"budget_{budget}_solutions.json")
            joint = solution["policies"]["joint"]
            routings = ("fastest", "resource_lp") if data.get("stage") == "e4" else (
                data.get("protocol", {}).get("candidate_routing", "resource_lp"),)
            for scenario in ("target_a", "target_b"):
                for routing in routings:
                    label = f"{item['budget_gib']}gib_joint"
                    if (label, scenario, routing) in declared_keys:
                        continue
                    sources = [r for r in measured if r["budget_bytes"] == budget and r["scenario"] == scenario]
                    for source in sources:
                        other = solution["policies"][source["label"].split("_", 1)[1]]
                        if layout_signature(other["selected"]) == layout_signature(joint["selected"]) and route_signature(
                            other["routing"][source["routing"]]["scenario_plans"][scenario]) == route_signature(
                            joint["routing"][routing]["scenario_plans"][scenario]):
                            declared.append({"label": label, "policy": "joint", "routing": routing, "scenario": scenario,
                                "source_phase": source["phase"], "new_observation": False,
                                "inferred_from_policy_dedup": True})
                            break
        except (KeyError, ValueError, FileNotFoundError) as exc:
            errors.append({"policy_dedup": item, "error": repr(exc)})
    for alias in declared:
        try:
            source = next(r for r in measured if r["phase"] == alias["source_phase"])
            if alias.get("new_observation") is not False or alias["scenario"] != source["scenario"] or not source["measurement_valid"]:
                raise ValueError("alias source is not a valid matching observation")
            budget = source["budget_bytes"]
            solution = load(path / f"budget_{budget}_solutions.json")
            a = solution["policies"][source["label"].split("_", 1)[1]]
            b = solution["policies"][alias["policy"]]
            scenario = alias["scenario"]
            if layout_signature(a["selected"]) != layout_signature(b["selected"]):
                raise ValueError("alias physical placement differs")
            if route_signature(a["routing"][source["routing"]]["scenario_plans"][scenario]) != route_signature(
                    b["routing"][alias["routing"]]["scenario_plans"][scenario]):
                raise ValueError("alias ordered executable route differs")
            result.append(dict(source, label=alias["label"], routing=alias["routing"],
                measurement_kind="reference_alias_not_new_observation", source_phase=source["phase"],
                original_planning_s=alias.get("original_planning_s"),
                placement_solve_wall_s=alias.get("placement_solve_wall_s")))
        except (KeyError, ValueError, StopIteration, FileNotFoundError) as exc:
            errors.append({"alias": alias, "error": repr(exc)})
    return result, errors


def decision_analysis(path, data):
    comparison, errors, evidence = data.get("comparison", {}), [], []
    try:
        previous = Path(comparison["run"])
        if sha(previous / "summary.json") != comparison["summary_sha256"]:
            raise ValueError("referenced e4 summary fingerprint changed")
        old = load(previous / "summary.json")
        if old.get("stage") != "e4" or old.get("status") != "passed" or old.get("cleanup_errors"):
            raise ValueError("referenced run is not an accepted e4 execution")
        budget = int(data["protocol"]["budget_gib"]) << 30
        current_solution = load(path / f"budget_{budget}_solutions.json")
        old_solution = load(previous / f"budget_{budget}_solutions.json")
        current_case = load(path / f"budget_{budget}_case.json")
        old_case = load(previous / f"budget_{budget}_case.json")
        greedy, joint = (current_solution["policies"][policy] for policy in ("greedy", "joint"))
        if layout_signature(greedy["selected"]) != layout_signature(joint["selected"]):
            raise ValueError("current placement decisions are not identical")
        for scenario in ("target_a", "target_b"):
            if route_signature(greedy["routing"][data["protocol"]["baseline_routing"]]["scenario_plans"][scenario]) != route_signature(
                    joint["routing"][data["protocol"]["candidate_routing"]]["scenario_plans"][scenario]):
                raise ValueError("current ordered route decisions are not identical")
        for key in ("profile", "tier_budgets"):
            if current_case[key] != old_case[key]:
                raise ValueError(f"reference {key} differs")
        fields = ("id", "identity", "tp", "off", "n", "worker", "tier", "numa", "epoch", "generation")
        base = lambda c: sorted([[r.get(k) for k in fields] for r in c["base"]])
        topology = lambda c: [[w[k] for k in ("worker", "node", "gpu", "numa")] for w in c["workers"]]
        if base(current_case) != base(old_case) or topology(current_case) != topology(old_case):
            raise ValueError("reference source base or topology differs")
        if not any(file.startswith("main_lib/") for file in data.get("code_sha256", {})):
            raise ValueError("measured product fingerprints are missing")
        for file, value in data.get("code_sha256", {}).items():
            if file.startswith("main_lib/") and old.get("code_sha256", {}).get(file) != value:
                raise ValueError("reference measured product code differs")
        keys = set()
        for sample in comparison["samples"]:
            row = next(r for r in old["cases"] if r["phase"] == sample["source_phase"])
            if row["status"] != "passed" or not row["correct"] or not row["verification"]["passed"] or \
                    row["timing"]["load_s"] != sample["T_ready_s"] or row["scenario"] != sample["scenario"]:
                raise ValueError("reference sample correctness, metric or target differs")
            a = old_solution["policies"][row["label"].split("_", 1)[1]]
            b = current_solution["policies"][sample["policy"]]
            if layout_signature(a["selected"]) != layout_signature(b["selected"]) or route_signature(
                a["routing"][row["routing"]]["scenario_plans"][row["scenario"]]) != route_signature(
                b["routing"][sample["routing"]]["scenario_plans"][sample["scenario"]]):
                raise ValueError("reference executable decision differs")
            keys.add((sample["policy"], sample["scenario"]))
            evidence.append(sample)
        if keys != {(p, s) for p in ("greedy", "joint") for s in ("target_a", "target_b")}:
            raise ValueError("reference does not cover both policies and targets")
        if len(comparison["samples"]) != 4 or data.get("decision") != "unchanged" or data.get("new_gpu_restore_count") != 0 or \
                data.get("cases") or data.get("speedup_claim") is not None:
            raise ValueError("decision-only run must have zero new GPU samples and no speedup claim")
    except (KeyError, ValueError, StopIteration, FileNotFoundError) as exc:
        errors.append(repr(exc))
    complete = not errors and not data.get("cleanup_errors") and not data.get("source_hash_mismatches")
    return {"kind": "decision_only", "stage": "e5", "run_id": path.name, "path": str(path),
        "execution_status": data.get("status"), "scientific_status": "no_decision_increment" if complete else "invalid_or_incomplete_reference",
        "performance_status": "not_measured", "decision_status": "unchanged" if complete else "unverified",
        "complete": complete, "generation_passed": complete, "generation_scope": "referenced e4 samples; no new generation",
        "new_gpu_restore_count": 0, "speedup_claim": None, "reference": comparison.get("run"),
        "reference_summary_sha256": comparison.get("summary_sha256"), "reference_samples": evidence,
        "reference_errors": errors, "cleanup_errors": data.get("cleanup_errors", []),
        "error": "; ".join(errors) or data.get("error"),
        "limitations": ["same placement and executable routes imply no decision increment in this registered scenario",
            "referenced timings are existing e4 observations, not new e5 measurements",
            "identical transfer decisions do not prove identical planning overhead or end-to-end performance"]}


def ipc_analysis(path, data):
    protocol_path = path / "protocol.json"
    protocol = load(protocol_path) if protocol_path.exists() else {}
    expected = protocol.get("repetitions", 2)
    hits = []
    for hit in data.get("hits", []):
        timing = hit.get("timing", {})
        ready = hit.get("primary_T_ready_s", timing.get("load_s"))
        per_host = {host: value.get("load_s") for host, value in timing.get("per_host", {}).items()}
        releases = hit.get("release_evidence", {})
        hits.append({"repetition": hit.get("repetition"), "phase": hit.get("phase"),
            "status": hit.get("status", "incomplete"), "primary_T_ready_s": ready,
            "per_host_T_ready_s": per_host,
            "request_to_ready_ack_s": hit.get("request_to_ready_ack_s", hit.get("request_to_weights_ready_s")),
            "export_s": hit.get("export_s"), "resume_rpc_s": hit.get("resume_rpc_s"),
            "request_to_first_token_s": hit.get("request_to_first_token_s"),
            "generation_correct": hit.get("correct"),
            "consumer_release_verified": bool(releases) and all(
                value.get("last_event", {}).get("event") == "release_end" for value in releases.values())})
    retirement = data.get("owner_retirement", [])
    allocator_tol = protocol.get("release_tolerance_bytes")
    physical_tol = protocol.get("physical_free_tolerance_bytes")
    reclaim = []
    for row in retirement:
        one = {"worker": row["worker"]}
        for name in ("allocated", "reserved", "physical_free", "catalog_used"):
            before, after = row.get(name + "_before"), row.get(name + "_after")
            one[name + "_delta_bytes"] = after - before if number(before) and number(after) else None
        one["allocator_reclaimed"] = (all(number(one[k]) and one[k] <= allocator_tol for k in
            ("allocated_delta_bytes", "reserved_delta_bytes"))) if number(allocator_tol) else None
        one["physical_reclaimed"] = (one["physical_free_delta_bytes"] >= -physical_tol
            if number(one["physical_free_delta_bytes"]) and number(physical_tol) else None)
        one["catalog_reclaimed"] = one["catalog_used_delta_bytes"] == 0
        reclaim.append(one)
    counts_complete = len(hits) == expected and all(hit["status"] == "passed" for hit in hits)
    generation_ok = passed([h["generation_correct"] for h in hits]) and len(hits) == expected
    timing_ok = len(hits) == expected and all(number(h["primary_T_ready_s"]) or
        (bool(h["per_host_T_ready_s"]) and number(h["request_to_ready_ack_s"])) for h in hits)
    ownership_ok = len(reclaim) == protocol.get("tp", len(reclaim)) and passed([
        r["allocator_reclaimed"] is True and r["physical_reclaimed"] is True and r["catalog_reclaimed"]
        for r in reclaim])
    owner_verified = data.get("owner_setup", {}).get("verification", {}).get("passed")
    function_ok = counts_complete and generation_ok and owner_verified is True
    base_unchanged = data.get("base_sources_unchanged", data.get("cpu_sources_unchanged"))
    acceptance = function_ok and timing_ok and ownership_ok and base_unchanged is True and \
        data.get("protected_catalog_unchanged") is True and not data.get("cleanup_errors") and \
        not data.get("source_hash_mismatches") and data.get("status") == "passed"
    return {"kind": "ipc_gate", "stage": "e1", "run_id": path.name, "path": str(path),
        "method": "海光 SGLang HBM 常驻 HIP IPC 适配基线；eager warm engine；非上游原版 daemon 或新进程启动成绩",
        "model": data.get("model", protocol.get("model", "B")), "execution_status": data.get("status", "unknown"),
        "scientific_status": "baseline_functional_gate_passed" if acceptance else "incomplete_or_failed_gate",
        "complete": counts_complete, "gate_passed": acceptance, "generation_passed": generation_ok,
        "owner_content_verified": owner_verified, "timing_complete": timing_ok,
        "owner_reclamation_passed": ownership_ok, "base_sources_unchanged": base_unchanged,
        "base_source_bytes_by_tier": data.get("base_source_bytes_by_tier"),
        "owner_build_source_bytes_by_tier": data.get("owner_build_source_bytes_by_tier"),
        "expected_hits": expected, "observed_hits": len(hits), "hits": hits, "owner_retirement": reclaim,
        "owner_bytes": data.get("owner_bytes"), "owner_setup_request_s": data.get("owner_setup_request_s"),
        "cleanup_errors": data.get("cleanup_errors", []), "error": data.get("error"),
        "limitations": ["two cycles validate lifecycle; they are not a performance confidence interval",
            "cross-host coordinator acknowledgment includes export, RPC and final acknowledgment; not a global clock subtraction",
            "preserved DRAM/SSD source checks compare metadata, not a post-run byte rehash",
            "disk fingerprints alone do not certify code already loaded into persistent processes"]}


def paired_comparison(rows, baseline_label, baseline_routing, candidate_label, candidate_routing, name,
                      identical_layout=False):
    chosen = []
    for scenario in ("target_a", "target_b"):
        left = [r for r in rows if r["label"] == baseline_label and r["routing"] == baseline_routing
                and r["scenario"] == scenario and r["measurement_valid"]]
        right = [r for r in rows if r["label"] == candidate_label and r["routing"] == candidate_routing
                 and r["scenario"] == scenario and r["measurement_valid"]]
        if len(left) != 1 or len(right) != 1:
            chosen.append({"scenario": scenario, "status": "incomplete"})
            continue
        a, b = left[0], right[0]
        same_budget = all(a[k] == b[k] and a[k] is not None for k in
                          ("budget_bytes", "base_page_rounded_bytes"))
        chosen.append({"scenario": scenario, "status": "complete" if same_budget else "budget_mismatch",
            "same_budget": same_budget, "same_actual_cache_charge": a["cache_page_rounded_bytes"] == b["cache_page_rounded_bytes"],
            "baseline_cache_bytes": a["cache_page_rounded_bytes"], "candidate_cache_bytes": b["cache_page_rounded_bytes"],
            "shared_observation": a.get("phase") is not None and a.get("phase") == b.get("phase"),
            **delta(a["T_ready_s"], b["T_ready_s"])})
    complete = all(row["status"] == "complete" for row in chosen)
    aggregate = delta(statistics.mean(r["baseline_s"] for r in chosen),
                      statistics.mean(r["candidate_s"] for r in chosen)) if complete else delta(None, None)
    shared = complete and all(row.get("shared_observation") for row in chosen)
    reference_delta = aggregate
    if shared:
        aggregate = {**aggregate, "saved_s": None, "improvement_pct": None}
    return {"name": name, "baseline": [baseline_label, baseline_routing], "candidate": [candidate_label, candidate_routing],
            "complete": complete, "identical_placement": identical_layout,
            "measurement_alias": baseline_label == candidate_label and baseline_routing == candidate_routing,
            "by_scenario": chosen, "equal_weight_mean": aggregate, "reference_arithmetic": reference_delta,
            "performance_delta_identified": complete and not shared,
            "interpretation": "single observations; negative and zero improvements retained"}


def matrix_analysis(path, data):
    stage, protocol = data.get("stage", "unknown"), data.get("protocol", {})
    builds = {b["label"]: b for b in data.get("builds", [])}
    rows = []
    for case in data.get("cases", []):
        build = builds.get(case["label"], {})
        elapsed = case.get("timing", {}).get("load_s")
        verified = case.get("verification", {}).get("passed")
        valid = case.get("status") == "passed" and case.get("correct") is True and verified is True and number(elapsed)
        rows.append({"label": case["label"], "scenario": case.get("scenario"), "routing": case.get("routing"),
            "phase": case.get("phase"), "status": case.get("status", "incomplete"), "measurement_valid": valid,
            "T_ready_s": elapsed, "request_to_ready_ack_s": case.get("request_to_ready_ack_s"),
            "request_to_first_token_s": case.get("request_to_first_token_s"), "generation_correct": case.get("correct"),
            "content_verified": verified, "budget_bytes": build.get("budget_bytes"),
            "base_logical_bytes": build.get("base_logical_bytes"), "base_page_rounded_bytes": build.get("base_page_rounded_bytes"),
            "cache_page_rounded_bytes": build.get("cache_page_rounded_bytes"), "build_wall_s": build.get("build_wall_s"),
            "source_bytes": case.get("flow", {}).get("source_bytes"),
            "breakdown_s": {k: case.get("flow", {}).get(k) for k in
                ("prepare_s", "execute_wall_s", "verify_and_publish_s", "coordinator_wall_s")}})
    aliases, alias_errors = alias_rows(path, data, rows)
    comparison_rows = rows + aliases
    expected_keys, comparisons, dedup, solution_evidence = [], [], [], []
    if stage == "e3":
        labels = ["concentrated", "distributed"] + (["replicated", "hybrid"] if protocol.get("extra_layouts") else [])
        expected_keys = [(label, scenario, "resource_lp") for label in labels for scenario in ("target_a", "target_b")]
        for label in labels[1:]:
            comparisons.append(paired_comparison(comparison_rows, "concentrated", "resource_lp", label, "resource_lp", label + "_vs_concentrated"))
    else:
        budgets = ([int(v) for v in str(protocol.get("budgets_gib", "0,8,16")).split(",")]
                   if stage == "e6" else [int(protocol.get("budget_gib", 8))])
        recorded_dedup = {row["budget_gib"] for row in data.get("deduplicated", [])}
        for gib in budgets:
            solution_path = path / f"budget_{gib * (1 << 30)}_solutions.json"
            solution = load(solution_path) if solution_path.exists() else None
            equal = (layout_signature(solution["policies"]["greedy"]["selected"]) ==
                     layout_signature(solution["policies"]["joint"]["selected"])) if solution else False
            solution_evidence.append({"budget_gib": gib, "path": str(solution_path), "present": solution is not None,
                "identical_layout_verified": equal,
                "dedup_consistent": gib not in recorded_dedup or equal})
            if equal or gib in recorded_dedup:
                dedup.append({"budget_gib": gib, "same_physical_layout": equal,
                              "runner_deduplicated": gib in recorded_dedup,
                              "interpretation": "joint reuses greedy observations; no additional sample" if gib in recorded_dedup
                              else "same placement, distinct routing measurements retained"})
            labels = [f"{gib}gib_greedy", f"{gib}gib_joint"]
            baseline_routing = protocol.get("baseline_routing", "resource_lp")
            candidate_routing = protocol.get("candidate_routing", "resource_lp")
            for label in labels:
                routings = ["fastest", "resource_lp"] if stage == "e4" else [
                    baseline_routing if label.endswith("greedy") else candidate_routing]
                expected_keys += [(label, scenario, routing) for scenario in ("target_a", "target_b") for routing in routings]
            greedy, joint = f"{gib}gib_greedy", f"{gib}gib_joint"
            if stage == "e4":
                for routing in routings:
                    comparisons.append(paired_comparison(comparison_rows, greedy, routing, joint, routing,
                        f"{gib}GiB_placement_at_{routing}", equal))
                for policy, label in (("greedy", greedy), ("joint", joint)):
                    comparisons.append(paired_comparison(comparison_rows, label, "fastest", label, "resource_lp",
                        f"{gib}GiB_routing_at_{policy}", True))
                comparisons.append(paired_comparison(comparison_rows, greedy, "fastest", joint, "resource_lp",
                    f"{gib}GiB_combined", equal))
            else:
                comparisons.append(paired_comparison(comparison_rows, greedy, baseline_routing, joint, candidate_routing,
                    f"{gib}GiB_joint_vs_greedy", equal))
    observed_keys = [(r["label"], r["scenario"], r["routing"]) for r in rows if r["measurement_valid"]]
    alias_keys = [(r["label"], r["scenario"], r["routing"]) for r in aliases]
    expected_set, covered_set = set(expected_keys), set(observed_keys + alias_keys)
    missing = [list(key) for key in sorted(expected_set - covered_set)]
    unexpected = [list(key) for key in sorted(covered_set - expected_set)]
    source_phases = {r["phase"] for r in rows if r["measurement_valid"]}
    duplicate_measurements = len(observed_keys) != len(set(observed_keys)) or len(source_phases) != len(observed_keys)
    duplicate_coverage = len(observed_keys + alias_keys) != len(covered_set)
    complete = bool(expected_set) and not missing and not unexpected and not duplicate_measurements and \
        not duplicate_coverage and not alias_errors
    budget_valid = bool(builds) and all(number(b.get("cache_page_rounded_bytes")) and number(b.get("budget_bytes"))
        and b["cache_page_rounded_bytes"] <= b["budget_bytes"] for b in builds.values())
    cleanup_ok = not data.get("cleanup_errors") and not data.get("owned_remaining") and bool(data.get("final_slots")) and \
        all(s.get("parked") is True for s in data.get("final_slots", {}).values())
    initial = {w["worker"]: w for w in data.get("initial_inventory", [])}
    final = {w["worker"]: w for w in data.get("final_inventory", [])}
    resources = []
    for wid in sorted(initial.keys() & final.keys()):
        before, after = initial[wid], final[wid]
        resources.append({"worker": wid, "node": before["node"], "gpu": before["gpu"],
            "allocated_delta_bytes": after["hbm_allocated_bytes"] - before["hbm_allocated_bytes"],
            "reserved_delta_bytes": after["hbm_reserved_bytes"] - before["hbm_reserved_bytes"],
            "physical_free_delta_bytes": after["headroom_bytes"]["hbm"] - before["headroom_bytes"]["hbm"],
            "catalog_hbm_delta_bytes": after["used_bytes"]["hbm"] - before["used_bytes"]["hbm"]})
    catalog_reclaimed = bool(resources) and all(r["catalog_hbm_delta_bytes"] == 0 for r in resources)
    accepted = complete and budget_valid and cleanup_ok and catalog_reclaimed and data.get("status") == "passed" and \
        not data.get("source_hash_mismatches") and all(s["present"] and s["dedup_consistent"] for s in solution_evidence)
    curves = []
    if stage == "e6":
        for label, build in builds.items():
            subset = [r for r in rows if r["label"] == label and r["measurement_valid"]]
            if {r["scenario"] for r in subset} != {"target_a", "target_b"} or len(subset) != 2:
                curves.append({"label": label, "status": "incomplete", "budget_bytes": build.get("budget_bytes")})
                continue
            mean = statistics.mean(r["T_ready_s"] for r in subset)
            policy = label.split("_", 1)[1]
            routing = subset[0]["routing"]
            origin = [r for r in rows if r["budget_bytes"] == 0 and r["routing"] == routing and r["measurement_valid"]
                      and r["base_page_rounded_bytes"] == build["base_page_rounded_bytes"]]
            origin_labels = sorted({r["label"] for r in origin})
            if origin_labels:
                own_origin = f"0gib_{policy}"
                origin_label = own_origin if own_origin in origin_labels else origin_labels[0]
                origin = [r for r in origin if r["label"] == origin_label]
            origin_mean = statistics.mean(r["T_ready_s"] for r in origin) if len(origin) == 2 else None
            references = set([label.replace("greedy", "joint")] if label.endswith("greedy") and
                             build["budget_bytes"] // (1 << 30) in recorded_dedup else [])
            phases = {r["phase"] for r in subset}
            references.update(f"{a['label']}/{a['routing']}" for a in aliases if a["source_phase"] in phases)
            curves.append({"label": label, "routing": routing, "status": "complete", "budget_bytes": build["budget_bytes"],
                "actual_cache_bytes": build["cache_page_rounded_bytes"], "fixed_dram_bytes": build["base_page_rounded_bytes"],
                "mean_T_ready_s": mean, "worst_T_ready_s": max(r["T_ready_s"] for r in subset),
                "build_wall_s": build.get("build_wall_s"), "delta_from_zero_budget": delta(origin_mean, mean),
                "aliases": sorted(references)})
        curves.sort(key=lambda row: (row.get("budget_bytes", -1), row["label"]))
    return {"kind": "placement_matrix", "stage": stage, "run_id": path.name, "path": str(path),
        "execution_status": data.get("status", "unknown"), "complete": complete,
        "scientific_status": "exploratory_primary_metric_evidence" if accepted else "incomplete_or_failed_matrix",
        "expected_measurements": len({r["phase"] for r in comparison_rows if r["measurement_valid"] and
            (r["label"], r["scenario"], r["routing"]) in expected_set}) + len(missing),
        "expected_configurations": len(expected_set), "covered_configurations": len(covered_set & expected_set),
        "valid_measurements": len(source_phases), "alias_count": len(aliases), "missing": missing, "unexpected": unexpected,
        "duplicate_configuration_coverage": duplicate_coverage,
        "duplicate_measurements": duplicate_measurements, "generation_passed": passed([r["generation_correct"] for r in rows]),
        "content_verification_passed": passed([r["content_verified"] for r in rows]), "budget_valid": budget_valid,
        "cleanup_passed": cleanup_ok, "catalog_reclaimed": catalog_reclaimed, "physical_reclamation": "reported_not_prequalified",
        "resource_deltas": resources, "physical_high_water_verified": data.get("physical_high_water_verified", False),
        "prewarm": data.get("prewarm"),
        "rows": rows, "comparisons": comparisons, "deduplicated": dedup, "solution_evidence": solution_evidence,
        "route_aliases": aliases, "route_alias_errors": alias_errors,
        "budget_curve": curves,
        "zero_budget_origin_present": any(c.get("budget_bytes") == 0 and c["status"] == "complete" for c in curves) if stage == "e6" else None,
        "cleanup_errors": data.get("cleanup_errors", []), "error": data.get("error"),
        "limitations": ["one observation per policy/target; differences are exploratory, not a statistical ranking",
            "same budget means equal permitted extra HBM plus equal charged DRAM base; actual selected HBM is also reported",
            "target allocations, runtime memory and unrelated owners coexist; high-water verification is separately flagged",
            "identical physical layouts share measurements; aliases do not increase sample counts",
            "zero and negative improvement points are retained"]}


def event_window(path):
    stamps = []
    events = path / "events.jsonl"
    if events.exists():
        for line in events.read_text().splitlines():
            row = json.loads(line)
            if number(row.get("wall_time")):
                stamps.append(int(row["wall_time"] * 1e9))
        if stamps:
            return min(stamps), max(stamps), "recorded coordinator event envelope; initial preflight may precede it"
    for file in (path / "metrics").rglob("*.jsonl"):
        for line in file.read_text().splitlines():
            row = json.loads(line)
            if number(row.get("wall_ns")):
                stamps.append(row["wall_ns"])
    return ((min(stamps), max(stamps), "rank metric event envelope; initial cache build/prewarm may precede it")
            if stamps else (None, None, "no wall-clock event envelope available"))


def resource_samples(run, monitor_snapshots, output):
    begin, end, scope = event_window(Path(run["path"]))
    resource = {"window_begin_wall_ns": begin, "window_end_wall_ns": end, "scope": scope,
                "exact_high_water": False, "sample_interval_target_s": 1,
                "clock_alignment": "host wall clocks; inter-host clock error not calibrated", "nodes": []}
    if begin is None:
        return resource
    for source, raw in monitor_snapshots.items():
        rows = []
        for line in raw.splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        during = [r for r in rows if begin <= r["wall_ns"] <= end]
        earlier = [r for r in rows if r["wall_ns"] < begin]
        later = [r for r in rows if r["wall_ns"] > end]
        selected = (earlier[-1:] + during + later[:1])
        node = Path(source).name.removesuffix("_hbm.jsonl")
        destination = output / "resource_samples" / run["run_id"] / Path(source).name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("".join(json.dumps(r) + "\n" for r in selected))
        row = {"node": node, "source": source, "snapshot": str(destination.resolve()),
               "inside_sample_count": len(during), "sample_sha256": sha(destination),
               "bracket_samples": len(selected) - len(during)}
        if during:
            cards = sorted({card for r in during for card in r["vram_used_bytes"]})
            totals = [sum(r["vram_used_bytes"].values()) for r in during]
            currents = [r.get("cgroup", {}).get("memory.current") for r in during]
            available = [r.get("host_memory_bytes", {}).get("MemAvailable") for r in during]
            baseline = earlier[-1] if earlier else during[0]
            row.update(host=during[0]["host"], first_sample_wall_ns=during[0]["wall_ns"],
                last_sample_wall_ns=during[-1]["wall_ns"],
                per_card_sampled_max_bytes={card: max(r["vram_used_bytes"].get(card, 0) for r in during) for card in cards},
                simultaneous_total_sampled_max_bytes=max(totals), simultaneous_total_sampled_min_bytes=min(totals),
                baseline_total_bytes=sum(baseline["vram_used_bytes"].values()),
                sampled_max_minus_baseline_bytes=max(totals) - sum(baseline["vram_used_bytes"].values()),
                cgroup_memory_current_sampled_max_bytes=max((v for v in currents if number(v)), default=None),
                host_mem_available_sampled_min_bytes=min((v for v in available if number(v)), default=None))
            before_oom = baseline.get("cgroup", {}).get("memory.events", {}).get("oom_kill")
            after_oom = during[-1].get("cgroup", {}).get("memory.events", {}).get("oom_kill")
            row["cgroup_oom_kill_counter_delta"] = after_oom - before_oom if number(before_oom) and number(after_oom) else None
        resource["nodes"].append(row)
    return resource


def fmt(value):
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "PASS" if value else "未通过/不完整"
    return f"{value:.6f}" if isinstance(value, float) else str(value)


def table(headers, rows):
    return "\n".join(["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"] +
                     ["| " + " | ".join(fmt(v).replace("|", "\\|") for v in row) + " |" for row in rows])


def candidate_mean_display(comparison):
    shared = comparison.get("measurement_alias") or comparison.get("performance_delta_identified") is False or \
        any(row.get("shared_observation") for row in comparison.get("by_scenario", []))
    return "引用同一phase；未独立计时" if shared else comparison["equal_weight_mean"]["candidate_s"]


def render(result):
    text = ["# e1–e6 实验分析快照", "", "原始运行只读；本报告不替代正式实验总记录。正的节省表示加速，负值表示变慢。",
            "", f"输入在分析期间保持不变：{fmt(result['inputs_unchanged'])}。", ""]
    for run in result["runs"]:
        text += [f"## {run['stage']} / {run['run_id']}", "", f"执行状态：`{run['execution_status']}`；科研状态：`{run['scientific_status']}`。",
                 "", f"原始结果：[summary.json]({run['path']}/summary.json)。", "",
                 f"生成正确性：{fmt(run['generation_passed'])}；完整性：{fmt(run['complete'])}。", ""]
        if run["kind"] == "ipc_gate":
            text += [run["method"] + "。", "",
                     f"owner 内容校验：{fmt(run['owner_content_verified'])}；owner 回收验收：{fmt(run['owner_reclamation_passed'])}。", "",
                     table(["循环", "同机 T_ready(s)", "协调端 ready 确认(s)", "fresh export(s)", "首token(s)", "生成"],
                           [[h["repetition"], h["primary_T_ready_s"], h["request_to_ready_ack_s"], h["export_s"],
                             h["request_to_first_token_s"], h["generation_correct"]] for h in run["hits"]]), ""]
            hosts = [[h["repetition"], host, value] for h in run["hits"] for host, value in h["per_host_T_ready_s"].items()]
            if hosts:
                text += [table(["循环", "主机", "主机内权重窗口(s)"], hosts), "",
                         "跨主机的协调确认窗口包括导出、RPC 和返回确认；不冒充跨机器统一时钟下的权重窗口。", ""]
        elif run["kind"] == "decision_only":
            text += ["科研结论：本场景没有新增放置或取数决策；引用已有 e4 正确性证据，新增 GPU 测量为 0。" if run["complete"]
                else "引用核验尚未通过，不形成决策等价或性能结论。", "",
                f"引用源：[e4 summary]({run['reference']}/summary.json)，指纹核对：{fmt(run['complete'])}。", "",
                table(["策略", "取数", "目标", "引用phase", "历史T_ready(s)"],
                    [[r["policy"], r["routing"], r["scenario"], r["source_phase"], r["T_ready_s"]]
                     for r in run["reference_samples"]]), "", "这些引用不增加样本数，也不构成新的性能提升结论。", ""]
        else:
            text += [f"独立有效测量：{run['valid_measurements']}；逻辑配置覆盖：{run['covered_configurations']}/{run['expected_configurations']}；引用别名：{run['alias_count']}；预算合规：{fmt(run['budget_valid'])}；清理：{fmt(run['cleanup_passed'])}。", "",
                     table(["布局", "目标", "取数", "预算GiB", "实占缓存GiB", "T_ready(s)", "生成", "全量校验"],
                           [[r["label"], r["scenario"], r["routing"], r["budget_bytes"] / (1 << 30) if number(r["budget_bytes"]) else None,
                             r["cache_page_rounded_bytes"] / (1 << 30) if number(r["cache_page_rounded_bytes"]) else None,
                             r["T_ready_s"], r["generation_correct"], r["content_verified"]] for r in run["rows"]]), "",
                     table(["对照", "对照均值(s)", "候选均值(s)", "节省(s)", "改善%", "决策覆盖", "同布局"],
                           [[c["name"], c["equal_weight_mean"]["baseline_s"], candidate_mean_display(c),
                             c["equal_weight_mean"]["saved_s"], c["equal_weight_mean"]["improvement_pct"], c["complete"],
                             "是（布局相同）" if c["identical_placement"] else "否（布局不同）"] for c in run["comparisons"]]), "",
                     "“决策覆盖”表示配置由有效测量或已核验的相同决策引用覆盖，不表示候选已独立计时或性能等价。", ""]
            if run["stage"] == "e6":
                text += [f"0 GiB 原点已测：{fmt(run['zero_budget_origin_present'])}。", "",
                    table(["布局", "预算GiB", "均值(s)", "相对原点节省(s)", "相对原点改善%", "测量别名"],
                          [[c["label"], c.get("budget_bytes", 0) / (1 << 30), c.get("mean_T_ready_s"),
                            c.get("delta_from_zero_budget", {}).get("saved_s"), c.get("delta_from_zero_budget", {}).get("improvement_pct"),
                            ",".join(c.get("aliases", []))] for c in run["budget_curve"]]), ""]
        if run.get("error"):
            text += [f"失败记录：`{run['error']}`", ""]
        if run.get("cleanup_errors"):
            text += ["清理问题：" + "; ".join(run["cleanup_errors"]), ""]
        if run.get("resource_samples"):
            sample = run["resource_samples"]
            text += ["1 秒资源采样（包含同设备其他进程；采样最大值不等于精确峰值）：", "",
                table(["节点", "窗口内样本", "同时总HBM最大GiB", "较窗口前样本增加GiB", "新增OOM kill"],
                    [[r["node"], r["inside_sample_count"],
                      r.get("simultaneous_total_sampled_max_bytes", 0) / (1 << 30) if r["inside_sample_count"] else None,
                      r.get("sampled_max_minus_baseline_bytes", 0) / (1 << 30) if r["inside_sample_count"] else None,
                      r.get("cgroup_oom_kill_counter_delta")] for r in sample["nodes"]]), "",
                "关联窗口：" + sample["scope"] + "。主机间 wall clock 误差未校准。", ""]
        text += ["测量边界：", ""] + ["- " + note for note in run["limitations"]] + [""]
    return "\n".join(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--monitor-dir", type=Path, help="Optional append-only one-second resource monitor directory")
    args = parser.parse_args()
    if any(args.out.resolve().is_relative_to(path.resolve()) for path in args.run_dir):
        raise ValueError("analysis output must be outside each immutable source run")
    args.out.mkdir(parents=True, exist_ok=False)
    manifest, runs = {}, []
    monitors = {str(p.resolve()): p.read_bytes() for p in sorted(args.monitor_dir.glob("*_hbm.jsonl"))} if args.monitor_dir else {}
    for directory in args.run_dir:
        directory = directory.resolve()
        paths = [p for p in directory.rglob("*") if p.is_file() and p.suffix in (".json", ".jsonl", ".txt", ".log")]
        manifest.update({str(p): {"sha256": sha(p), "bytes": p.stat().st_size} for p in paths})
        data = load(directory / "summary.json")
        if data.get("status") == "decision_only" and data.get("stage") == "e5":
            previous = Path(data["comparison"]["run"])
            paths = [p for p in previous.rglob("*") if p.is_file() and p.suffix in (".json", ".jsonl", ".txt", ".log")]
            manifest.update({str(p.resolve()): {"sha256": sha(p), "bytes": p.stat().st_size} for p in paths})
            runs.append(decision_analysis(directory, data))
        elif "hits" in data and data.get("experiment_id") == "e1":
            runs.append(ipc_analysis(directory, data))
        elif data.get("stage") in ("e3", "e4", "e5", "e6") and "cases" in data:
            runs.append(matrix_analysis(directory, data))
        else:
            raise ValueError(f"unsupported schema: {directory}; historical audits and synthetic pilots need their own analyzers")
        if monitors:
            runs[-1]["resource_samples"] = resource_samples(runs[-1], monitors, args.out)
    changed = [p for p, row in manifest.items() if not Path(p).exists() or sha(p) != row["sha256"]]
    monitor_manifest = {}
    for source, raw in monitors.items():
        with Path(source).open("rb") as stream:
            unchanged = stream.read(len(raw)) == raw
        if not unchanged:
            changed.append(source)
        monitor_manifest[source] = {"snapshot_prefix_bytes": len(raw), "snapshot_prefix_sha256": hashlib.sha256(raw).hexdigest(),
                                    "prefix_unchanged": unchanged, "append_after_snapshot_allowed": True}
    result = {"schema_version": 1, "development": "v0.1.23.3", "task": "task2", "created_unix": time.time(),
              "inputs_unchanged": not changed, "inputs_changed": changed, "runs": runs}
    if changed:
        for run in runs:
            run["scientific_status"] = "incomplete_input_changed_during_analysis"
    manifest["analyzer"] = {"path": str(Path(__file__).resolve()), "sha256": sha(__file__)}
    manifest["append_only_monitors"] = monitor_manifest
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (args.out / "analysis.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    (args.out / "report.md").write_text(render(result))
    print(json.dumps({"out": str(args.out), "inputs_unchanged": not changed,
                      "runs": [{"run_id": r["run_id"], "scientific_status": r["scientific_status"]} for r in runs]}))


if __name__ == "__main__":
    main()
