"""Independent offline evidence checks; no GPU, RPC, SSH, or product validators."""
import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pair_statistics(flows, singles):
    observed = max(f["receiver_end_ns"] for f in flows) - min(f["receiver_start_ns"] for f in flows)
    expected = max(single["receiver_end_ns"] - single["receiver_start_ns"] for single in singles)
    total = sum(f["bytes"] for f in flows)
    skew = max(f["receiver_start_ns"] for f in flows) - min(f["receiver_start_ns"] for f in flows)
    skew_fraction = skew / min(single["receiver_end_ns"] - single["receiver_start_ns"] for single in singles)
    return {"pair_common_window_GBps": total / observed,
            "independent_expected_makespan_s": expected / 1e9,
            "observed_makespan_s": observed / 1e9,
            "independent_makespan_ratio": expected / observed,
            "pressure_exploration_flag": expected / observed <= .85 and skew_fraction <= .05,
            "start_skew_fraction_of_shortest_single": skew_fraction,
            "start_control_qualified": skew_fraction <= .05,
            "receiver_start_skew_s": (max(f["receiver_start_ns"] for f in flows) - min(f["receiver_start_ns"] for f in flows)) / 1e9,
            "per_flow_slowdown": {str(f["flow_id"]): (f["receiver_end_ns"] - f["receiver_start_ns"]) /
                                  (single["receiver_end_ns"] - single["receiver_start_ns"])
                                  for f, single in zip(flows, singles)}}


class Audit:
    def __init__(self):
        self.checks = []
        self.rates = []
        self.inputs = {}
        self.observations = 0

    def check(self, label, passed, detail=None):
        self.checks.append({"check": label, "passed": bool(passed), "detail": detail})

    def read(self, path):
        path = Path(path).resolve()
        data = path.read_bytes()
        self.inputs[str(path)] = hashlib.sha256(data).hexdigest()
        return json.loads(data)

    def flow(self, flow, expected=None):
        name = str(flow.get("flow_id"))
        start, end = flow["receiver_start_ns"], flow["receiver_end_ns"]
        intervals = flow["chunk_intervals"]
        valid = (type(start) is int and type(end) is int and end > start
                 and len(intervals) == flow["iterations"])
        previous = start
        for left, right in intervals:
            valid &= type(left) is int and type(right) is int and previous <= left < right <= end
            previous = right
        self.check(f"flow:{name}:completed_ordered_intervals", valid)
        self.check(f"flow:{name}:completion_sync", flow.get("completion") == "cuda_stream_synchronize")
        self.check(f"flow:{name}:full_traffic_accounted", flow["bytes"] == flow["buffer_bytes"] * flow["iterations"])
        check = flow["correctness"]
        self.check(f"flow:{name}:full_buffer_SHA", check.get("passed") is True
                   and check.get("sha256") == check.get("expected_sha256")
                   and check.get("verified_bytes") == flow["buffer_bytes"])
        if flow.get("source_sha256") is not None:
            self.check(f"flow:{name}:source_target_SHA", flow["source_sha256"] == check["sha256"])
        if expected:
            self.check(f"flow:{name}:registered_bytes", flow["bytes"] == expected["bytes_per_flow"]
                       and flow["iterations"] == expected["iterations"]
                       and flow["buffer_bytes"] == expected["buffer_bytes"])
        rate = flow["bytes"] / (end - start) if end > start else None
        if flow.get("empirical_effective_GBps") is not None:
            self.check(f"flow:{name}:reported_rate_recomputed", rate is not None
                       and math.isclose(rate, flow["empirical_effective_GBps"], rel_tol=1e-8))
        return rate

    def calibration(self, path):
        data = self.read(path)
        self.check("calibration:schema", data.get("schema_version") == "topology_calibration.v1")
        self.check("calibration:completed", data.get("status") == "passed")
        self.check("calibration:not_capacity_upper_bound", data.get("capacity_is_physical_upper_bound") is False)
        self.check("calibration:preservation", data.get("before_after_preservation", {}).get("passed") is True)
        self.check("calibration:cleanup", data.get("cleanup", {}).get("passed") is True)
        self.check("calibration:resource_admission", data.get("resource_admission", {}).get("passed") is True)
        for relative, digest in data.get("code_sha256", {}).items():
            source = ROOT / relative
            self.check(f"calibration:code_current_hash:{relative}", source.exists() and file_hash(source) == digest)
        session_path = Path(data.get("session", ""))
        self.check("calibration:session_current_hash", session_path.is_file()
                   and file_hash(session_path) == data.get("session_overlay_sha256"))
        observations = data.get("observations", [])
        if isinstance(observations, dict):
            observations = list(observations.values())
        ids = [r["case_id"] for r in observations]
        self.check("calibration:unique_configuration_observations", len(set(ids)) == len(ids))
        self.observations = len(ids)
        protocol_path = Path(path).parent / "protocol.json"
        protocol = self.read(protocol_path) if protocol_path.exists() else data
        expected = {r["case_id"]: r for r in protocol.get("cases", [])}
        self.check("calibration:registered_matrix_complete", bool(expected) and set(ids) == set(expected), ids)
        self.check("calibration:session_snapshot_identity", protocol.get("session_overlay_sha256") == data.get("session_overlay_sha256")
                   and protocol.get("topology_id") == data.get("topology_id"))
        self.protection(Path(path).parent, data)
        singles = {}
        pairs = []
        for row in observations:
            case = row["case_id"]
            flows = row["flows"]
            signature = [f["flow_id"] for f in flows]
            spec = expected.get(case)
            self.check(f"{case}:registered_flow_ids", bool(spec) and sorted(signature) == sorted(spec["flow_ids"])
                       and len(signature) == len(set(signature)))
            rates = [self.flow(f, spec) for f in flows]
            self.check(f"{case}:one_sample", row.get("samples") == 1)
            if row.get("kind") == "network":
                self.nic(row, data.get("binding", []))
            prefix = case.rsplit("_", 2)[0] if "_single_" in case else case.removesuffix("_pair")
            if len(flows) == 1:
                f = flows[0]
                singles[(prefix, f["flow_id"])] = (rates[0], f)
            if len(flows) == 2:
                domains = {f["clock_domain"] for f in flows}
                self.check(f"{case}:common_receiver_clock", len(domains) == 1)
                shared = max(0, min(f["receiver_end_ns"] for f in flows) - max(f["receiver_start_ns"] for f in flows))
                fraction = shared / min(f["receiver_end_ns"] - f["receiver_start_ns"] for f in flows)
                self.check(f"{case}:overlap_at_least_80pct", fraction >= .8, fraction)
                makespan = max(f["receiver_end_ns"] for f in flows) - min(f["receiver_start_ns"] for f in flows)
                aggregate = sum(f["bytes"] for f in flows) / makespan
                pairs.append((prefix, case, flows, aggregate, fraction))
        for prefix, case, flows, aggregate, fraction in pairs:
            matched = all((prefix, f["flow_id"]) in singles for f in flows)
            self.check(f"{case}:matched_singles", matched)
            if matched:
                match_fields = ("src", "dst", "bytes", "buffer_bytes", "iterations", "completion")
                for f in flows:
                    old = singles[(prefix, f["flow_id"])][1]
                    self.check(f"{case}:matched_definition:{f['flow_id']}", all(f[k] == old[k] for k in match_fields))
                single_sum = sum(singles[(prefix, f["flow_id"])][0] for f in flows)
                stats = pair_statistics(flows, [singles[(prefix, f["flow_id"])][1] for f in flows])
                self.rates.append(dict(stats, case_id=case, single_rate_sum_GBps=single_sum,
                                       overlap_fraction=fraction, capacity_is_physical_upper_bound=False))
                if case == "network_pair":
                    reported = data.get("shared_resource", {})
                    self.check("network_pair:reported_independence_ratio_recomputed",
                               math.isclose(reported.get("independence_completion_ratio", -1), stats["independent_makespan_ratio"], rel_tol=1e-8))
                    if reported.get("gate_status") == "qualified":
                        self.check("network_pair:qualified_requires_controlled_slowdown", stats["pressure_exploration_flag"])
        return data

    def freeze(self, path, calibration=None):
        manifest = self.read(path)
        parent = Path(path).parent
        self.check("freeze:schema", manifest.get("schema_version") == "topology_freeze.v1")
        self.check("freeze:no_GPU_samples", manifest.get("new_GPU_samples") == 0)
        claimed = manifest["manifest_sha256"]
        self.check("freeze:self_hash", canonical({k: v for k, v in manifest.items() if k != "manifest_sha256"}) == claimed)
        for name, record in manifest["inputs"].items():
            snapshot = parent / "inputs" / (name + ".json")
            self.read(snapshot)
            self.check(f"freeze:input_snapshot_hash:{name}", file_hash(snapshot) == record["sha256"])
            original = Path(record["path"])
            self.check(f"freeze:input_current_hash:{name}", original.exists() and file_hash(original) == record["sha256"])
        for relative, digest in manifest["code_sha256"].items():
            original = ROOT / relative
            self.check(f"freeze:code_current_hash:{relative}", original.exists() and file_hash(original) == digest)
        for row in manifest["cases"]:
            name = row["case_id"]
            case = self.read(parent / "cases" / (name + ".json"))
            self.check(f"{name}:case_hash", canonical(case) == row["case_sha256"])
            self.case(case)
            gate = row["calibration_gate"]
            qualified = gate["physical_shared_case_qualified"] or gate["application_profile_qualified"]
            if qualified:
                self.check(f"{name}:qualified_calibration_has_evidence", calibration is not None
                           and calibration.get("status") == "passed"
                           and calibration.get("before_after_preservation", {}).get("passed") is True
                           and calibration.get("cleanup", {}).get("passed") is True)
            if gate["physical_shared_case_qualified"]:
                shared = calibration.get("shared_resource", {}) if calibration else {}
                self.check(f"{name}:application_executor_evidence", shared.get("application_executor") == "serial"
                           and shared.get("application_executor_contention_verified") is True
                           and bool(shared.get("application_evidence_refs"))
                           and bool(shared.get("application_executor_code_sha256")))
            if not gate["application_profile_qualified"]:
                self.check(f"{name}:provisional_preview_label", row["profile_source"] == "functional_provisional_assumption")
            preview_path = parent / "previews" / (name + ".json")
            if preview_path.exists():
                preview = self.read(preview_path)
                self.check(f"{name}:preview_not_measured", preview.get("new_GPU_samples") == 0
                           and preview.get("estimated_values_are_performance_evidence") is False)
                for record in preview["rows"]:
                    expected_identity = "hip_adaptation" if record["method"] == "sglang_ipc_hip" else "mechanism"
                    self.check(f"{name}:method_identity:{record['method']}", record.get("identity") == expected_identity)
                    if record.get("plan"):
                        self.plan(case, record)
        return manifest

    def nic(self, row, bindings):
        name = row["case_id"]
        receiver = [b for b in bindings if b.get("rank") in (2, 3)]
        self.check(f"{name}:NIC_binding_receivers", len(receiver) == 2 and all(b.get("passed") is True for b in receiver))
        ports = receiver[0].get("actual_hca_ports", []) if receiver else []
        self.check(f"{name}:unique_actual_receive_port", len(ports) == 1
                   and all(b.get("actual_hca_ports") == ports for b in receiver))
        if len(ports) != 1:
            return
        counter = f"/sys/class/infiniband/{ports[0]['hca']}/ports/{ports[0]['port']}/counters/port_rcv_data"
        flows = row["flows"]
        before = min((f["nic_snapshot_before"] for f in flows), key=lambda s: s["monotonic_ns"])
        after = max((f["nic_snapshot_after"] for f in flows), key=lambda s: s["monotonic_ns"])
        initial, final = before["values"].get(counter, {}), after["values"].get(counter, {})
        valid = (before["clock_domain"] == after["clock_domain"] == flows[0]["clock_domain"]
                 and before["monotonic_ns"] < after["monotonic_ns"]
                 and initial.get("unit_bytes") == final.get("unit_bytes") == 4
                 and type(initial.get("raw")) is int and type(final.get("raw")) is int
                 and 0 <= initial["raw"] <= final["raw"])
        self.check(f"{name}:raw_actual_port_counter_valid", valid)
        if valid:
            delta, payload = (final["raw"] - initial["raw"]) * 4, sum(f["bytes"] for f in flows)
            self.check(f"{name}:actual_port_receive_payload", delta >= payload, {"delta": delta, "payload": payload})
            reported = row.get("actual_nic_payload_evidence", {})
            self.check(f"{name}:reported_NIC_payload_recomputed", reported.get("counter") == counter
                       and reported.get("direction") == "receive" and reported.get("expected_payload_bytes") == payload
                       and reported.get("actual_counter_delta_bytes") == delta)

    def protection(self, parent, data):
        before_path, after_path = parent / "inventory_before.json", parent / "inventory_after.json"
        self.check("calibration:raw_pre_post_present", before_path.exists() and after_path.exists())
        if not before_path.exists() or not after_path.exists():
            return
        before, after = self.read(before_path), self.read(after_path)
        fields = ("id", "identity", "worker", "tp", "off", "n", "tier", "epoch", "generation", "state", "leases")
        def catalog(inv):
            return [{"worker": w["worker"], "epoch": w["epoch"], "session": w["session"],
                     "transactions": w["transactions"],
                     "replicas": sorted([{k: r.get(k) for k in fields} for r in w["replicas"]], key=lambda r: r["id"])} for w in inv["workers"]]
        self.check("calibration:independent_catalog_preservation", catalog(before) == catalog(after))
        for old, new in zip(before["workers"], after["workers"]):
            self.check(f"calibration:worker{old['worker']}:actual_transaction_clean", not new.get("transactions")
                       and not new.get("transaction_phases"))
            self.check(f"calibration:worker{old['worker']}:physical_reclamation", old["headroom_bytes"]["hbm"] - new["headroom_bytes"]["hbm"] <= 128 << 20)
            for field in ("hbm_allocated_bytes", "hbm_reserved_bytes", "used_bytes", "staging_bytes"):
                self.check(f"calibration:worker{old['worker']}:preserved:{field}", field in old and field in new and old[field] == new[field])
        for node, old in before["nodes"].items():
            new = after["nodes"][node]
            spec_fields = ("spec", "spec_sha256", "registered")
            self.check(f"calibration:{node}:protected_PID_identity", bool(new["protected_processes"])
                       and all(p["passed"] for p in new["protected_processes"])
                       and [{k: p[k] for k in spec_fields} for p in old["protected_processes"]]
                       == [{k: p[k] for k in spec_fields} for p in new["protected_processes"]])
            self.check(f"calibration:{node}:parked_commands_preserved", old["parked_commands"] == new["parked_commands"])
        current = {r["endpoint"]: r for r in after["protected_legacy_endpoints"]}
        for old in before["protected_legacy_endpoints"]:
            if old["accessible"]:
                new = current.get(old["endpoint"], {})
                self.check(f"calibration:legacy:{old['endpoint']}", new.get("accessible") is True and old["signature"] == new.get("signature"))
        states = [p for rows in data.get("cleanup", {}).get("process_states", {}).values() for p in rows]
        self.check("calibration:owned_cleanup_complete", bool(states) and len(states) == len(data.get("processes", []))
                   and all(p.get("actual", {}).get("live") is False for p in states))
        owned = {p["registered"].get("spec"): p for p in states}
        for process in data.get("processes", []):
            proof = owned.get(process["spec"], {})
            raw = process.get("start_reply")
            started = json.loads(raw) if isinstance(raw, str) else raw
            registered = proof.get("registered", {})
            self.check(f"calibration:owned_PID_registration:{process['rank']}", isinstance(started, dict)
                       and started.get("pid") == registered.get("pid")
                       and started.get("start_time") == registered.get("start_time")
                       and registered.get("spec") == process["spec"])

    def case(self, case):
        name = case["case_id"]
        budget, base = case["budget"], case["base"]
        rounded = lambda r: (r["n"] + 4095) // 4096 * 4096
        self.check(f"{name}:fixed_development", case["development"] == "v0.1.23.3/task2")
        self.check(f"{name}:exclusive_scenes", case["scenario_semantics"] == "mutually_exclusive_restores")
        self.check(f"{name}:rank_identities", case["format_contract"]["processed_snapshot_id"]
                   == [b["identity"] for b in sorted(base, key=lambda b: b["tp"])])
        self.check(f"{name}:complete_base_charged", budget["base_dram_bytes"]
                   == sum(b.get("allocated_bytes", rounded(b)) for b in base))
        self.check(f"{name}:target_charged", budget["target_allocation_bytes"] == sum(rounded(b) for b in base))
        self.check(f"{name}:common_schedule", case["execution_contract"]["schedule"] == "rank_interleaved")
        self.check(f"{name}:common_validation_boundary", case["execution_contract"]["verification_position"] == "after_weights_ready")
        self.check(f"{name}:bounded_repetitions", case["protocol"]["repetitions"] == 1)
        candidates = {c["id"]: c for c in case["candidates"]}
        self.check(f"{name}:unique_candidate_id", len(candidates) == len(case["candidates"]))
        for layout in case["fixed_layouts"]:
            selected = [candidates[i] for i in layout["candidate_ids"]]
            self.check(f"{name}:{layout['name']}:total_budget", sum(rounded(c) for c in selected) <= budget["tier_budgets"]["hbm"])
            for location, cap in budget["location_budgets"].items():
                self.check(f"{name}:{layout['name']}:location:{location}", sum(rounded(c) for c in selected
                           if f"{c['worker']}:hbm" == location) <= cap)
        for candidate in candidates.values():
            matching = [b for b in base if b["identity"] == candidate["identity"] and b["tp"] == candidate["tp"]]
            self.check(f"{name}:candidate_covered:{candidate['id']}", candidate.get("preview_only") is True
                       and len(matching) == 1 and candidate["n"] > 0 and matching[0]["off"] <= candidate["off"]
                       and candidate["off"] + candidate["n"] <= matching[0]["off"] + matching[0]["n"])

    def plan(self, case, record):
        plan = record["plan"]
        name = f"{case['case_id']}:{record['layout']}:{record['scenario']}:{record['method']}"
        signature = canonical([{k: o[k] for k in ("target", "identity", "tp", "off", "n", "src_w", "dst_w", "replica_id")} for o in plan["ops"]])
        self.check(f"{name}:operation_signature", signature == record["operation_signature"])
        layout = next(l for l in case["fixed_layouts"] if l["name"] == record["layout"])
        sources = {r["id"]: r for r in case["base"] + case["candidates"]
                   if r in case["base"] or r["id"] in layout["candidate_ids"]}
        for target in plan["targets"]:
            ops = [o for o in plan["ops"] if o["target"] == target["key"]]
            cursor = target["off"]
            valid = True
            for op in ops:
                source = sources.get(op["replica_id"], {})
                valid &= (op["off"] == cursor and op["n"] > 0 and op["identity"] == target["identity"]
                          and op["tp"] == target["tp"] and op["dst_w"] == target["worker"]
                          and source.get("identity") == op["identity"] and source.get("tp") == op["tp"]
                          and source.get("worker") == op["src_w"] and source.get("off", -1) <= op["off"]
                          and op["off"] + op["n"] <= source.get("off", 0) + source.get("n", 0))
                cursor += op["n"]
            self.check(f"{name}:exact_coverage:{target['key']}", valid and cursor == target["off"] + target["n"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--freeze", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.calibration and not args.freeze:
        parser.error("at least one input is required")
    audit = Audit()
    calibration = audit.calibration(args.calibration) if args.calibration else None
    manifest = audit.freeze(args.freeze, calibration) if args.freeze else None
    for path, digest in list(audit.inputs.items()):
        audit.check(f"input_stable:{path}", file_hash(path) == digest)
    result = {"schema_version": "topology_independent_audit.v1",
              "passed": all(c["passed"] for c in audit.checks), "checks": audit.checks,
              "inputs_sha256": audit.inputs, "rates": audit.rates,
              "independent_new_GPU_samples": 0, "referenced_calibration_observations": audit.observations,
              "freeze_cases": len(manifest["cases"]) if manifest else 0,
              "scope": "offline audit; throughput is empirical, host windows do not prove kernel occupancy"}
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    lines = ["# 拓扑阶段独立离线验收", "", f"验收：{'通过' if result['passed'] else '存在未通过门槛'}；检查{len(audit.checks)}项，新增GPU样本0。", "",
             f"引用校准配置观察{audit.observations}项；冻结场景{result['freeze_cases']}项。引用/CPU预览均不增加性能样本。", "",
             "| 并发配置 | 单流速率和GB/s | 同一窗口聚合GB/s | 独立预期/实测makespan | 调用区间overlap | 探索压力信号 |",
             "| --- | ---: | ---: | ---: | ---: | --- |"]
    for row in audit.rates:
        lines.append(f"| {row['case_id']} | {row['single_rate_sum_GBps']:.4f} | {row['pair_common_window_GBps']:.4f} | {row['independent_makespan_ratio']:.4f} | {row['overlap_fraction']:.4f} | {row['pressure_exploration_flag']} |")
    lines += ["", "单流实测速率和不是容量上界。并发调用区间仍包含等待；summary另列每流slowdown和start skew。起始skew超过最短匹配单流耗时5%时保持conditional。校准与应用执行器身份不同则物理场景保持conditional。完整模型收益需独立e4/e5验收。", ""]
    failed = [c for c in audit.checks if not c["passed"]]
    lines.extend(f"- 未通过：{c['check']}；{c['detail']}" for c in failed)
    (args.out / "report.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"passed": result["passed"], "checks": len(audit.checks), "out": str(args.out)}))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
