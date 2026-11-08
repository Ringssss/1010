"""Offline audit primitives; never contact workers or initialize accelerators."""
import copy
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[6]


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def review_freeze(path):
    path = Path(path).resolve()
    manifest = read(path)
    checks = []
    def check(name, condition):
        checks.append(dict(name=name, passed=bool(condition)))
    payload = copy.deepcopy(manifest)
    expected = payload.pop("manifest_sha256")
    check("frozen_manifest_canonical_hash", canonical(payload) == expected)
    check("frozen_CPU_evidence_zero_GPU", manifest["new_GPU_samples"] == 0
          and manifest["GPU_or_SSH_mutations"] is False)
    for name, row in manifest["inputs"].items():
        check("frozen_input_" + name, sha(path.parent / "inputs" / (name + ".json")) == row["sha256"])
        check("source_input_" + name, sha(row["path"]) == row["sha256"])
    for source, expected in manifest["code_sha256"].items():
        check("frozen_source_" + source, sha(ROOT / source) == expected)
    for row in manifest["cases"]:
        case = read(path.parent / "cases" / (row["case_id"] + ".json"))
        check("frozen_case_" + row["case_id"], canonical(case) == row["case_sha256"])
    return dict(checks=checks, manifest_path=str(path), manifest_file_sha256=sha(path),
                manifest_canonical_sha256=manifest["manifest_sha256"], new_GPU_samples=0)


def allocation_ledger(selected, budget):
    """Charge physical replicas independently, including duplicate logical regions."""
    ids = [row["id"] for row in selected]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate allocation id")
    tiers, locations = {}, {}
    for row in selected:
        n = row["n"]
        if type(n) is not int or n <= 0:
            raise ValueError("allocation bytes must be a positive integer")
        charged = (n + 4095) // 4096 * 4096
        tier = row["tier"]
        key = str(row["worker"]) + ":" + tier
        tiers[tier] = tiers.get(tier, 0) + charged
        locations[key] = locations.get(key, 0) + charged
    if any(tier not in budget["tier_budgets"] or n > budget["tier_budgets"][tier]
           for tier, n in tiers.items()):
        raise ValueError("global allocation budget exceeded")
    if any(key not in budget["location_budgets"] or n > budget["location_budgets"][key]
           for key, n in locations.items()):
        raise ValueError("location allocation budget exceeded")
    return dict(charged_bytes=sum(tiers.values()), logical_copy_bytes=sum(r["n"] for r in selected),
                tiers=tiers, locations=locations)


def receiver_envelope(rows):
    """Compare RPC envelopes only within one explicit host and boot clock domain."""
    if not rows:
        raise ValueError("receiver envelope missing")
    if len({(row["host"], row["boot_id"]) for row in rows}) != 1:
        raise ValueError("cross-host or cross-boot monotonic comparison")
    if not rows[0]["host"] or not rows[0]["boot_id"]:
        raise ValueError("explicit clock domain required")
    if len({row["worker"] for row in rows}) != len(rows):
        raise ValueError("duplicate receiver envelope")
    if any(type(row["start_ns"]) is not int or type(row["end_ns"]) is not int
           or row["end_ns"] <= row["start_ns"] for row in rows):
        raise ValueError("invalid RPC envelope boundaries")
    start = min(row["start_ns"] for row in rows)
    end = max(row["end_ns"] for row in rows)
    return dict(makespan_ns=end-start,
                start_skew_ns=max(row["start_ns"] for row in rows)-start,
                envelope_overlap_ns=max(0, min(row["end_ns"] for row in rows)
                                        - max(row["start_ns"] for row in rows)),
                scope="receiver execute HTTP RPC envelopes; includes dispatch and response",
                actual_worker_monotonic=None, physical_shared_claim_qualified=False)


def review_placement(summary_path, freeze_path):
    """Check saved CPU selections and restore plans against immutable opportunities."""
    summary = read(summary_path)
    directory = Path(freeze_path).parent
    case = read(directory / "cases" / (summary["case_id"] + ".json"))
    candidates = {r["id"]: r for r in case["candidates"]}
    checks, ledgers, signatures = [], {}, {}
    def check(name, condition):
        checks.append(dict(name=name, passed=bool(condition)))
    check("placement_CPU_only", summary["new_GPU_samples"] == 0
          and summary["measurement_status"] == "not_measured"
          and summary["prediction_ready"] is False
          and summary["physical_contention_claim_ready"] is False)
    check("identical_finite_candidate_opportunities", summary["candidate_ids"] == list(candidates))
    check("identical_global_location_budgets", summary["budget"] == case["budget"])
    check("identical_mutually_exclusive_demand", summary["scenario_weights"] ==
          {s["name"]: s["weight"] for s in case["restore_scenarios"]})
    check("common_interleaved_schedule", summary["schedule"] == "rank_interleaved")
    policies = dict(summary["policies"])
    for name, row in summary["policies"]["strong_simple"]["controls"].items():
        policies["control/" + name] = row
    for name, row in policies.items():
        selected = row["selected"]
        check(name + "_exact_frozen_candidates", all(r == candidates.get(r["id"]) for r in selected))
        ledger = allocation_ledger(selected, case["budget"])
        ledgers[name] = ledger
        check(name + "_copied_bytes_charged", row["ledger"]["allocated_hbm_bytes"] == ledger["charged_bytes"]
              and row["ledger"]["cache_build_logical_bytes"] == ledger["logical_copy_bytes"])
        available = {r["id"]: r for r in case["base"] + selected}
        evaluation = row["evaluation"]
        weighted = 0
        ordered = {}
        for scenario in case["restore_scenarios"]:
            plan = evaluation["plans"][scenario["name"]]
            check(name + "_" + scenario["name"] + "_fixed_targets", plan["targets"] == scenario["targets"])
            check(name + "_" + scenario["name"] + "_LP_no_fallback", plan["strategy"] == "resource_lp"
                  and not plan.get("fallback_reason"))
            weighted += plan["estimated_transfer_s"] * scenario["weight"]
            ops = plan["ops"]
            groups = [[op for op in ops if op["target"] == t["key"]] for t in plan["targets"]]
            interleaved = [group[i] for i in range(max(map(len, groups))) for group in groups if i < len(group)]
            check(name + "_" + scenario["name"] + "_common_order", ops == interleaved)
            legal = True
            for target in plan["targets"]:
                pos = target.get("off", 0)
                for op in sorted((o for o in ops if o["target"] == target["key"]), key=lambda o: o["off"]):
                    source = available.get(op["replica_id"])
                    legal &= bool(source and op["off"] == pos and op["n"] > 0
                                  and op["identity"] == target["identity"] == source["identity"]
                                  and op["tp"] == target["tp"] == source["tp"]
                                  and op["src_w"] == source["worker"] and op["dst_w"] == target["worker"]
                                  and source["off"] <= op["off"]
                                  and op["off"] + op["n"] <= source["off"] + source["n"])
                    pos += op["n"]
                legal &= pos == target.get("off", 0) + target["n"]
            check(name + "_" + scenario["name"] + "_legal_exact_coverage", legal)
            ordered[scenario["name"]] = [{k: op[k] for k in ("target", "identity", "tp", "off", "n", "src_w", "dst_w",
                                                            "replica_id", "kind", "resources")} for op in ops]
        expected = (max(p["estimated_transfer_s"] for p in evaluation["plans"].values()) if case["objective"] == "worst"
                    else weighted / sum(s["weight"] for s in case["restore_scenarios"]))
        check(name + "_common_aggregate_score", abs(expected - evaluation["objective_s"]) < 1e-10)
        signatures[name] = canonical(dict(layout=sorted((r["identity"], r["tp"], r["worker"], r["tier"], r["off"], r["n"])
                                                       for r in selected), ops=ordered))
    equal = signatures["strong_simple"] == signatures["joint"]
    check("same_decision_claim_independently_recomputed", summary["decisions_equal"] == equal)
    check("same_decision_alias_GPU_stop", not equal or any(a["action"] == "do_not_repeat_GPU" for a in summary["decision_aliases"]))
    scores = [r["evaluation"]["objective_s"] for r in summary["policies"]["strong_simple"]["controls"].values()]
    check("simple_winner_uses_common_final_score", abs(min(scores) - summary["policies"]["strong_simple"]["evaluation"]["objective_s"]) < 1e-10)
    return dict(checks=checks, case_id=case["case_id"], summary_sha256=sha(summary_path),
                decisions_equal=equal, allocation_ledgers=ledgers, new_GPU_samples=0)


def review_bridge(path):
    path = Path(path)
    directory = path if path.is_dir() else path.parent
    summary = read(directory / "summary.json")
    before, after = [read(directory / ("inventory_" + label + ".json")) for label in ("before", "after")]
    checks, observations = [], []
    def check(name, condition):
        checks.append(dict(name=name, passed=bool(condition)))
    check("bridge_passed", summary["status"] == "passed")
    check("no_full_model_or_true_worker_clock_claim", summary["T_ready"] is None
          and summary["worker_true_monotonic_window"] is None)
    check("direct_code_unchanged", summary["code_sha256"] == summary["code_sha256_after"])
    check("direct_inputs_unchanged", summary["input_sha256"] == summary["input_sha256_after"])
    check("direct_code_hashes", all(sha(ROOT / p) == h for p, h in summary["code_sha256"].items()))
    check("direct_input_hashes", all(sha(p) == h for p, h in summary["input_sha256"].items()))
    check("parent_node27_clock", summary["coordinator_domain"] == before["nodes"]["node27"]["clock_domain"])
    check("child_node29_clock", summary["target_coordinator_domain"] == before["nodes"]["node29"]["clock_domain"])
    initial = {w["worker"]: w for w in before["workers"]}
    final = {w["worker"]: w for w in after["workers"]}
    catalog_fields = ("epoch", "session", "transactions", "transaction_phases", "used_bytes",
                      "hbm_allocated_bytes", "hbm_reserved_bytes", "staging_bytes")
    for wid, a in initial.items():
        b = final[wid]
        check(f"worker{wid}_catalog_allocator_unchanged", all(a.get(k) == b.get(k) for k in catalog_fields))
        owned = [r for r in b["replicas"] if r["id"].startswith(summary["run_id"] + "/")]
        check(f"worker{wid}_all_owned_targets_retired", all(r["state"] == "RELEASED" and r["leases"] == 0 for r in owned))
        protected = [r for r in b["replicas"] if r not in owned]
        check(f"worker{wid}_protected_sources_and_leases", protected == a["replicas"])
        check(f"worker{wid}_physical_reclaimed", a["headroom_bytes"]["hbm"] - b["headroom_bytes"]["hbm"] <= summary["physical_hbm_free_tolerance_bytes"])
    for node, a in before["nodes"].items():
        b = after["nodes"][node]
        fields = ("spec", "spec_sha256", "registered")
        check(node + "_exact_protected_processes", all(r["passed"] for r in b["protected_processes"])
              and [{k: r[k] for k in fields} for r in a["protected_processes"]]
              == [{k: r[k] for k in fields} for r in b["protected_processes"]])
        check(node + "_parked_commands", a["parked_commands"] == b["parked_commands"])
    previous = {r["endpoint"]: r for r in before["protected_legacy_endpoints"] if r["accessible"]}
    current = {r["endpoint"]: r for r in after["protected_legacy_endpoints"]}
    check("legacy14_originally_reachable", len(previous) == 14)
    check("legacy14_catalog_preserved", all(current.get(ep, {}).get("accessible")
          and current[ep]["signature"] == row["signature"] for ep, row in previous.items()))
    runtime, raw_logs = {}, {}
    for node in before["nodes"]:
        facts = read(directory / (node + "_runtime_after.json"))
        runtime[node] = []
        raw_logs[node] = {}
        for keeper in facts["keepers"]:
            text = "\n".join(row.get("text", "") for row in keeper["logs"])
            ports = sorted(set((h, int(p)) for h, p in re.findall(r"(?:\[\d+\])?(shca[_\w]*):(\d+)", text)))
            channels = [line for line in text.splitlines() if "NET/IB" in line and ("send" in line or "receive" in line)]
            mode = ("GDR" if channels and all("GDRDMA" in line for line in channels)
                    else "non-GDR" if channels and all("GDRDMA" not in line for line in channels) else "unverified_or_mixed")
            gpu = int(keeper["gpu"])
            raw_logs[node][gpu] = text
            bus = "9000" if gpu == 0 else "36000"
            matched = [line for line in channels if f"0[{bus}] -> 1[{bus}]" in line
                       and "[send]" in line if node == "node27"] if node == "node27" else [
                line for line in channels if f"0[{bus}] -> 1[{bus}]" in line and "[receive]" in line and "nRanks 02" in line]
            complete = [line for line in matched if " comm " in line and "nRanks 02" in line]
            matched_mode = ("GDR" if complete and all("GDRDMA" in line for line in complete)
                            else "non-GDR" if complete and all("GDRDMA" not in line for line in complete)
                            else "unverified_or_mixed")
            runtime[node].append(dict(gpu=keeper["gpu"], actual_hca_ports=ports,
                                      all_communicator_transport=mode, channel_lines=len(channels),
                                      matched_bridge_channels=len(matched), matched_bridge_mode=matched_mode,
                                      complete_matched_bridge_channels=len(complete),
                                      incomplete_matched_channel_lines=[line for line in matched if line not in complete],
                                      matched_bus_ids=bus + "->" + bus, matched_lines=matched))
            check(node + "_keeper" + keeper["gpu"] + "_matched_bridge_pair_transport",
                  len(matched) == 32 and len(complete) >= 31
                  and matched_mode == ("GDR" if gpu == 0 else "non-GDR"))
            check(node + "_keeper" + keeper["gpu"] + "_log_hashes", all(
                hashlib.sha256(row["text"].encode()).hexdigest() == row["sha256"]
                for row in keeper["logs"] if "text" in row))
    cases = [read(directory / row["case"] / "summary.json") for row in summary["cases"]]
    check("bounded_case_set", [r["case"] for r in cases] == ["warmup_pairs", "single_A", "single_B", "pair"])
    sources = {r["id"]: r for w in before["workers"] for r in w["replicas"]}
    for row in cases:
        name = row["case"]
        case_dir = directory / name
        flow = row["flow"]
        plan = read(case_dir / "plan.json")
        check(name + "_passed", row["status"] == "passed" and not row.get("cleanup_errors"))
        check(name + "_serial_replica_targets", flow["executor"] == "serial" and all(t["mode"] == "replica" for t in plan["targets"]))
        check(name + "_exact_plan", dict(plan, executor="serial") == flow["plan"])
        check(name + "_all_SHA_verified", flow["verification"]["passed"]
              and flow["verification"]["checked_bytes"] == sum(op["n"] for op in plan["ops"]))
        for op in plan["ops"]:
            source = sources[op["replica_id"]]
            verified = flow["verification"]["operations"][str(op["op_id"])]
            check(name + f"_op{op['op_id']}_source_range_epoch_generation", source["tier"] == "dram"
                  and source["worker"] == op["src_w"] == op["tp"]
                  and op["dst_w"] == op["tp"] + 2 and op["source_epoch"] == source["epoch"]
                  and op["generation"] == source["generation"]
                  and source["off"] <= op["off"] and op["off"] + op["n"] <= source["off"] + source["n"]
                  and op["source_offset"] == op["off"] - source["off"])
            check(name + f"_op{op['op_id']}_full_SHA", verified["n"] == op["n"]
                  and bool(verified["source_sha256"]) and verified["source_sha256"] == verified["target_sha256"])
        selected_ids = {o["replica_id"] for o in plan["ops"]}
        for phase in ("prepare", "execute", "complete"):
            workers = read(case_dir / ("after_" + phase + ".json"))
            raw = {r["id"]: r for w in workers for r in w["replicas"]}
            check(name + "_" + phase + "_source_lease", all(raw[rid]["state"] == "VALID" and raw[rid]["leases"] == 1 for rid in selected_ids))
        events = read(case_dir / "rpc_events.json")
        receivers = {t["worker"] for t in plan["targets"]}
        rows = [r for r in events if r["operation"] == "execute" and r["worker"] in receivers]
        normalized = []
        for event in rows:
            host, boot, _ = event["clock_domain"].split("/")
            normalized.append(dict(host=host, boot_id=boot, worker=event["worker"],
                                   start_ns=event["begin_ns"], end_ns=event["end_ns"]))
        envelope = receiver_envelope(normalized)
        check(name + "_raw_receiver_RPC_window", abs(envelope["makespan_ns"] / 1e9 - row["receiver_rpc_envelope"]["elapsed_s"]) < 1e-12)
        phase_rows = {phase: [r for r in events if r["operation"] == phase] for phase in ("prepare", "execute", "verify_run", "complete")}
        check(name + "_phase_order_excludes_verify_prepare", all(max(r["end_ns"] for r in phase_rows[a]) <= min(r["begin_ns"] for r in phase_rows[b])
              for a, b in (("prepare", "execute"), ("execute", "verify_run"), ("verify_run", "complete"))))
        counters = read(case_dir / "receiver_counters.json")
        ports = []
        for hca, snap in counters["execute_before"].items():
            end = counters["execute_after"][hca]
            idle_a, idle_b = counters["idle_before"][hca], counters["idle_after"][hca]
            for counter, a in snap["values"].items():
                if "port_rcv_data" not in counter:
                    continue
                b = end["values"][counter]
                delta = (b["raw"] - a["raw"]) * a["unit_bytes"]
                idle_ns = idle_b["monotonic_ns"] - idle_a["monotonic_ns"]
                execute_ns = end["monotonic_ns"] - snap["monotonic_ns"]
                idle_delta = (idle_b["values"][counter]["raw"] - idle_a["values"][counter]["raw"]) * a["unit_bytes"]
                check(name + "_" + counter + "_counter_units_clock", a["unit_bytes"] == b["unit_bytes"] == 4 and delta >= 0
                      and snap["clock_domain"] == end["clock_domain"] == idle_a["clock_domain"] == idle_b["clock_domain"])
                ports.append(dict(counter=counter, receive_delta_bytes=delta,
                                  idle_background_estimated_fraction=(idle_delta * execute_ns / idle_ns / delta) if delta else None))
        actual_port = next(p for p in ports if p["counter"] == "/sys/class/infiniband/shca_0/ports/1/counters/port_rcv_data")
        payload = sum(op["n"] for op in plan["ops"])
        check(name + "_actual_selected_NIC_payload", actual_port["receive_delta_bytes"] >= payload)
        check(name + "_actual_selected_NIC_idle_background", actual_port["idle_background_estimated_fraction"] is not None
              and actual_port["idle_background_estimated_fraction"] <= .05)
        log_intervals, op_elapsed = [], {}
        for worker in flow["execution"]:
            wid = worker["worker"]
            if wid not in receivers:
                continue
            logrows = []
            for line in raw_logs["node29"][wid-2].splitlines():
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if event.get("tx") == flow["tx"]:
                    logrows.append(event)
            for record in worker["records"]:
                begin = [r for r in logrows if r.get("ev") == "cluster_op_begin" and r.get("op_id") == record["op_id"]]
                end = [r for r in logrows if r.get("ev") == "cluster_op_end" and r.get("op_id") == record["op_id"]]
                check(name + f"_receiver{wid}_raw_log_transaction", len(begin) == len(end) == 1)
                if len(begin) == len(end) == 1:
                    a, b = begin[0], end[0]
                    check(name + f"_receiver{wid}_log_elapsed_crosscheck", a["node"] == b["node"] == "node29"
                          and b["t"] >= a["t"] and abs(b["elapsed_s"] - record["elapsed_s"]) < .001
                          and abs(b["t"] - a["t"] - record["elapsed_s"]) < .001)
                    log_intervals.append(dict(worker=wid, begin_wall_s=a["t"], end_wall_s=b["t"],
                                              log_monotonic_elapsed_s=b["elapsed_s"], record_elapsed_s=record["elapsed_s"]))
            op_elapsed[str(wid)] = sum(r["elapsed_s"] for r in worker["records"])
        observations.append(dict(case=name, payload_bytes=sum(o["n"] for o in plan["ops"]),
                                 receiver_envelope=envelope, receiver_counters=ports,
                                 selected_port_counter=actual_port, receiver_wall_log_crosscheck=log_intervals,
                                 receiver_operation_elapsed_s=op_elapsed,
                                 worker_elapsed_s={str(w["worker"]): w["execute_s"] for w in flow["execution"]},
                                 prepare_s_instrumented=flow["prepare_s"], execute_s_instrumented=flow["execute_wall_s"],
                                 verify_publish_s_instrumented=flow["verify_and_publish_s"]))
    return dict(checks=checks, observations=observations, actual_runtime_binding=runtime,
                raw_run_sha256=sha(directory / "summary.json"), run=str(directory.resolve()),
                new_GPU_samples=0, cited_formal_configuration_observations=3,
                physical_shared_claim_qualified=False, application_profile_qualified=False)


def review_restore(path, freeze_path):
    """Independently review one full-model functional restore, not a speed contest."""
    directory = Path(path)
    summary = read(directory / "summary.json")
    case = read(Path(freeze_path).parent / "cases" / (summary["requested_frozen_case"] + ".json"))
    checks = []
    def check(name, condition):
        checks.append(dict(name=name, passed=bool(condition)))
    check("full_restore_passed", summary["status"] == "passed" and not summary["cleanup_errors"])
    check("one_functional_restore_only", len(summary["cases"]) == 1 and summary["selected_layout"] == "split_rank_prefix"
          and summary["mutually_exclusive_target"] == "target_b")
    check("not_physical_contention_or_prediction_claim", summary["prediction_ready"] is False
          and summary["physical_contention_claim_ready"] is False)
    check("input_freezes", all(sha(p) == h for p, h in summary["input_sha256"].items()))
    check("source_closure_freezes", all(sha(ROOT / p) == h for p, h in summary["code_sha256"].items()))
    check("no_owned_or_consumer_left", not summary["remaining_owned_ids"] and not summary["unreleased_endpoints"])
    before, after, cache_ready = [read(directory / ("inventory_" + label + ".json")) for label in ("before", "after", "cache_ready")]
    ledger = allocation_ledger(summary["selected_candidates"], case["budget"])
    build = summary["builds"][0]
    actual = build["actual_replicas"]
    check("8GiB_charged_6plus2_caps", ledger["charged_bytes"] == 8 << 30
          and ledger["locations"] == {"0:hbm": 6 << 30, "1:hbm": 2 << 30})
    check("duplicate_build_traffic_charged", sum(f["verification"]["checked_bytes"] for f in build["flows"]) == 8 << 30
          and summary["setup_ledger"]["unique_logical_bytes"] == 6 << 30
          and summary["setup_ledger"]["duplicated_logical_bytes"] == 2 << 30)
    raw_cache = {r["id"]: r for r in cache_ready["replicas"]}
    for selected, binding, built, transfer in zip(summary["selected_candidates"], build["candidate_runtime_bindings"], actual, build["flows"]):
        prefix = "cache_" + binding["actual_id"].rsplit("/", 1)[-1]
        check(prefix + "_actual_catalog", raw_cache[built["id"]] == built)
        check(prefix + "_current_incarnation", built["state"] == "VALID" and built["leases"] == 0
              and built["epoch"] == cache_ready["workers"][built["worker"]]["epoch"]
              and built["generation"] >= 1 and not built["id"].startswith("preview/"))
        check(prefix + "_exact_candidate_rebind", binding["candidate_id"] == selected["id"]
              and binding["actual_id"] == built["id"] and binding["epoch"] == built["epoch"]
              and binding["generation"] == built["generation"]
              and binding["allocated_bytes"] == built["allocated_bytes"] == (selected["n"] + 4095) // 4096 * 4096
              and all(built[k] == selected[k] for k in ("identity", "tp", "off", "n", "worker", "tier", "node", "gpu", "numa")))
        v = transfer["verification"]
        check(prefix + "_verified_build_before_rebind", v["passed"] and v["checked_bytes"] == selected["n"]
              and all(r["source_sha256"] == r["target_sha256"] for r in v["operations"].values()))
    check("complete_base_catalog_preserved", after["replicas"] == before["replicas"])
    for a, b in zip(before["workers"], after["workers"]):
        wid = a["worker"]
        check(f"worker{wid}_epochs_transactions_allocators", a["epoch"] == b["epoch"] and not b["transactions"]
              and a["used_bytes"] == b["used_bytes"] and a["hbm_allocated_bytes"] == b["hbm_allocated_bytes"]
              and a["hbm_reserved_bytes"] == b["hbm_reserved_bytes"] and a["staging_bytes"] == b["staging_bytes"])
        check(f"worker{wid}_physical_reclaim", a["headroom_bytes"]["hbm"] - b["headroom_bytes"]["hbm"] <= 256 << 20)
    legacy_a, legacy_b = [read(directory / ("protected_legacy_" + label + ".json")) for label in ("before", "after")]
    check("legacy14_catalog_protection", legacy_a == legacy_b and sum(r["status"] == "reachable" for r in legacy_a) == 14)
    row = summary["cases"][0]
    plan = row["flow"]["plan"]
    sources = {r["id"]: r for r in cache_ready["replicas"]}
    by_source, by_tier = {}, {}
    check("runtime_common_schedule", row["schedule"] == "rank_interleaved")
    queues = [[o for o in plan["ops"] if o["target"] == t["key"]] for t in plan["targets"]]
    check("raw_plan_rank_interleaved", plan["ops"] == [q[i] for i in range(max(map(len, queues))) for q in queues if i < len(q)])
    for target in plan["targets"]:
        pos = 0
        for op in sorted((o for o in plan["ops"] if o["target"] == target["key"]), key=lambda o: o["off"]):
            source = sources[op["replica_id"]]
            check(f"op{op['op_id']}_real_source_coverage", op["off"] == pos and op["n"] > 0
                  and op["identity"] == source["identity"] == target["identity"]
                  and op["tp"] == source["tp"] == target["tp"] and op["dst_w"] == target["worker"]
                  and op["source_epoch"] == source["epoch"] and op["generation"] == source["generation"]
                  and source["off"] <= op["off"] and op["off"] + op["n"] <= source["off"] + source["n"])
            pos += op["n"]
            by_source[source["id"]] = by_source.get(source["id"], 0) + op["n"]
            by_tier[source["tier"]] = by_tier.get(source["tier"], 0) + op["n"]
        check(target["key"] + "_full_rank_coverage", pos == target["n"])
    check("runtime_source_bytes", by_source == row["flow"]["source_bytes"])
    check("full_model_SHA", row["verification"]["passed"] and row["verification"]["checked_bytes"] == sum(by_source.values()) == 70329718112
          and all(r["n"] == next(o["n"] for o in plan["ops"] if o["op_id"] == r["op_id"])
                  and r["source_sha256"] == r["target_sha256"] for r in row["verification"]["operations"].values()))
    for worker in row["flow"]["execution"]:
        expected = [o for o in plan["ops"] if worker["worker"] in (o["src_w"], o["dst_w"])]
        check(f"runtime_worker{worker['worker']}_order", [o["op_id"] for o in expected] == [r["op_id"] for r in worker["records"]])
    session = read(Path(freeze_path).parent / "inputs/session.json")
    check("full_model_correct_output", row["correct"] and row["reply"]["text"] == session["models"]["B"]["reference"]["text"])
    times, execute_times = [], []
    for rank in range(2):
        path = directory / "metrics" / row["phase"] / (f"rank{rank}.jsonl")
        events = [json.loads(line) for line in path.read_text().splitlines()]
        selected = {event: [r for r in events if r["event"] == event] for event in
                    ("load_begin", "weights_ready", "target_execute_begin", "target_copy_complete")}
        check(f"rank{rank}_unique_raw_timing", all(len(rows) == 1 for rows in selected.values()))
        check(f"rank{rank}_raw_one_host", len({r["host"] for r in events}) == 1)
        begin, end = selected["load_begin"][0], selected["weights_ready"][0]
        times.append((begin["host"], begin["monotonic_ns"], end["monotonic_ns"]))
        execute_times.append((selected["target_execute_begin"][0]["monotonic_ns"], selected["target_copy_complete"][0]["monotonic_ns"]))
        check(f"rank{rank}_copy_inside_load", begin["monotonic_ns"] <= execute_times[-1][0] <= execute_times[-1][1] <= end["monotonic_ns"])
        release = row["release_evidence"]["release_evidence"][rank]
        check(f"rank{rank}_released", sha(release["path"]) == release["sha256"] and release["last_event"]["event"] == "release_end")
    check("same_host_load_monotonic", len({t[0] for t in times}) == 1)
    ready_s = (max(t[2] for t in times) - min(t[1] for t in times)) / 1e9
    check("T_ready_independently_recomputed", abs(ready_s - row["primary_T_ready_s"]) < 1e-12)
    return dict(checks=checks, source_bytes=by_source, source_tier_bytes=by_tier, actual_cache_ledger=ledger,
                T_ready_s=ready_s, copy_window_s=(max(t[1] for t in execute_times) - min(t[0] for t in execute_times)) / 1e9,
                cache_build_s=build["wall_s"], online_planning_s=row["online_planning_s"], prepare_s=row["flow"]["prepare_s"],
                post_ready_verification_s=row["post_ready_verification_s"], physical_peak_verified=False,
                cited_full_model_observations=1, new_GPU_samples=0, purpose="fixed8GiB layout functional smoke; no placement performance comparison")
