"""S4 fragmented cache and S5 software-sharing order control on retained TP2.

Reuse ScenarioRun's complete transfer, inference, parked and cleanup gates.
Registration is CPU-only; --execute is reserved for the coordinating agent.
"""
import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import resource
import time
import traceback

from run_scenarios import ScenarioRun
from runtime_support import ROOT, digest, load_state, new_run, select_base, target, write
from fluidckpt.cluster.planning import validate_plan

GIB = 1 << 30
PROTOCOL = Path(__file__).parent / "configs/scene45_protocol.json"


def schedule_config(protocol, scene, reverse_schedules=False):
    if reverse_schedules and scene != "S5":
        raise ValueError("--reverse-schedules is only valid for S5")
    config = copy.deepcopy(protocol[scene])
    if reverse_schedules:
        config["schedules"].reverse()
    return config


def operation_signature(op):
    # Preserve every semantic field, including IDs, source generation and resources.
    return json.dumps(op, sort_keys=True, separators=(",", ":"), allow_nan=False)


def control_signature(plan):
    # Only the per-request target key changes between restores on the same engine.
    return Counter(operation_signature(dict(o, target=f"rank:{o['tp']}")) for o in plan["ops"])


def reordered_plan(plan, schedule):
    validate_plan(plan)
    result = copy.deepcopy(plan)
    if schedule == "rank_interleaved":
        queues = [[o for o in plan["ops"] if o["target"] == t["key"]] for t in plan["targets"]]
        result["ops"] = [copy.deepcopy(q[i]) for i in range(max(map(len, queues))) for q in queues if i < len(q)]
    elif schedule != "target_major":
        raise ValueError("unknown operation schedule")
    if Counter(map(operation_signature, plan["ops"])) != Counter(map(operation_signature, result["ops"])):
        raise AssertionError("schedule changed source, range or operation semantics")
    for t in plan["targets"]:
        if ([o for o in plan["ops"] if o["target"] == t["key"]] !=
                [o for o in result["ops"] if o["target"] == t["key"]]):
            raise AssertionError("schedule changed per-target order")
    validate_plan(result)
    result["manual_schedule"] = schedule
    result["schedule_scope"] = "global operation permutation only; no optimized scheduling claim"
    return result


def dependency_report(plan):
    """Worker-local serial dependencies do not subtract clocks across hosts."""
    workers = sorted({w for o in plan["ops"] for w in (o["src_w"], o["dst_w"])})
    orders, edges = {}, []
    for w in workers:
        mine = [o for o in plan["ops"] if w in (o["src_w"], o["dst_w"])]
        orders[str(w)] = [o["op_id"] for o in mine]
        for a, b in zip(mine, mine[1:]):
            edges.append({"worker": w, "before": a["op_id"], "after": b["op_id"],
                          "before_target": a["target"], "after_target": b["target"],
                          "cross_target": a["target"] != b["target"]})
    return {"kind": "software_worker_serial_order", "global_order": [o["op_id"] for o in plan["ops"]],
            "worker_orders": orders, "serial_edges": edges,
            "cross_target_edges": [e for e in edges if e["cross_target"]],
            "physical_NIC_contention_measured": False}


def verify_runtime_order(plan, execution):
    seen = set()
    for worker in execution:
        wid = worker["worker"]
        if wid in seen:
            raise AssertionError("duplicate execution worker")
        seen.add(wid)
        expected = [o for o in plan["ops"] if wid in (o["src_w"], o["dst_w"])]
        records = worker["records"]
        if [r["op_id"] for r in records] != [o["op_id"] for o in expected]:
            raise AssertionError("runtime worker order differs from scheduled global order")
        for op, row in zip(expected, records):
            role = "both" if op["src_w"] == op["dst_w"] else "source" if wid == op["src_w"] else "target"
            if any(row[k] != v for k, v in {"role": role, "bytes": op["n"], "replica_id": op["replica_id"], "kind": op["kind"]}.items()):
                raise AssertionError("runtime operation metadata differs from frozen plan")
    if not {w for o in plan["ops"] for w in (o["src_w"], o["dst_w"])} <= seen:
        raise AssertionError("runtime evidence misses a participating worker")
    return {"passed": True, "checked_workers": sorted(seen), "kind": "runtime_worker_order_and_semantics"}


def selected_source_bytes(plan, ledger):
    tiers = {r["allocation_id"]: r["tier"] for r in ledger["allocations"]}
    total = Counter()
    for op in plan["ops"]:
        total[tiers[op["replica_id"]]] += op["n"]
    return {"hbm": total["hbm"], "dram": total["dram"]}


def cache_layout(model, cache_slot, config):
    rows = []
    for rank, position, off, n in config["assignments"]:
        source = model["sources"][rank]
        if source["tp"] != rank or off < 0 or n <= 0 or off + n > source["n"]:
            raise ValueError("cache extent exceeds processed rank snapshot")
        rows.append({"tp": rank, "identity": source["identity"], "worker": cache_slot["workers"][position],
                     "tier": "hbm", "off": off, "n": n})
    needed = Counter()
    for r in rows:
        needed[r["worker"]] += (r["n"] + 4095) // 4096 * 4096
    if sum(needed.values()) != 8 * GIB or len(needed) < 2 or max(needed.values()) > 4 * GIB:
        raise ValueError("layout must use exactly fragmented 8GiB with at most 4GiB per worker")
    return rows, dict(needed)


class OrderCluster:
    """Intercept planning only; all existing lifecycle/verification methods survive."""
    def __init__(self, cluster, runner):
        self.cluster, self.runner = cluster, runner

    def __getattr__(self, name):
        return getattr(self.cluster, name)

    def solve_restore(self, replicas, targets, workers, strategy="nearest"):
        plan = self.cluster.solve_restore(replicas, targets, workers, strategy)
        if len(targets) == 2 and all(t.get("mode") == "live" for t in targets):
            return self.runner.scheduled(plan)
        return plan


class LayoutScenarioRun(ScenarioRun):
    def __init__(self, args):
        self.protocol = json.loads(PROTOCOL.read_text())
        self.reverse_schedules = getattr(args, "reverse_schedules", False)
        self.config = schedule_config(self.protocol, args.scene, self.reverse_schedules)
        self.schedule = "target_major"
        self.canonical_ops = None
        super().__init__(args)
        self.cluster = OrderCluster(self.cluster, self)
        self.cache_slot = copy.deepcopy(self.state["slots"][args.cache_slot])
        self.layout, self.cache_needed = cache_layout(self.model, self.cache_slot, self.config)
        self.freeze[str(PROTOCOL.relative_to(ROOT))] = digest(PROTOCOL)
        self.result.update(experiment_id="e3", gate_kind="layout_control",
                           subprotocol=self.config.get("subprotocol", "fragmented_cache_mechanism_comparison"),
                           physical_resource_gate="not_measured", builds=[], planned_cache_layout=self.layout,
                           protocol_file=str(PROTOCOL), protocol_sha256=digest(PROTOCOL))
        self.result.update(variant="software_worker_serial" if args.scene == "S5" else "fragmented_cache",
                           reverse_schedules=self.reverse_schedules,
                           schedule_execution_order=list(self.config["schedules"]),
                           target_slot=self.target_slot, cache_slot=self.cache_slot,
                           budget={"extra_hbm_bytes": 8*GIB,
                               "location_extra_hbm_bytes": {f"{w}:hbm": 4*GIB for w in self.cache_needed},
                               "base_dram_logical_bytes": sum(s["n"] for s in self.model["sources"])})
        self.result["protocol"].update(source_availability="same complete retained DRAM plus actual charged 8GiB cache",
            layout=self.layout, schedules=self.config["schedules"],
            reverse_schedules=self.reverse_schedules, schedule_execution_order=list(self.config["schedules"]),
            eligibility_contract="S4", physical_resource_contention_measured=False,
            schedule_scope=self.config.get("scope"), method_boundaries=self.protocol["method_boundaries"],
            janus_host_budget_scope="S4 fixes shared 8GiB cache for all cases; Janus_host uses DRAM only; its standalone minimum HBM retention is not 8GiB")
        self.save()

    def preflight(self):
        super().preflight()
        wm = {w["worker"]: w for w in self.before}
        if self.cache_slot["endpoint"] != self.target_slot["endpoint"]:
            if (self.cache_slot.get("model", "B") != "B" or len(self.cache_slot["workers"]) != 2 or
                    len(set(self.cache_slot["workers"])) != 2 or
                    len({wm[w]["node"] for w in self.cache_slot["workers"]}) != 1):
                raise ValueError("cache slot must identify one complete same-host parked Qwen TP2 engine")
            self.result["parked_preflight"][self.cache_slot["endpoint"]] = self.parked_evidence(self.cache_slot)
        if self.args.scene == "S5" and set(self.cache_slot["workers"]) & set(self.target_slot["workers"]):
            raise ValueError("S5 requires distinct cache sources and target devices")
        for wid, n in self.cache_needed.items():
            w = wm[wid]
            output = sum(s["n"] for s, dst in zip(self.model["sources"], self.target_slot["workers"]) if dst == wid)
            if n > w["budget_bytes"]["hbm"] - w["used_bytes"]["hbm"]:
                raise MemoryError("cache catalog admission failed")
            if n + output + (self.args.guard_mib << 20) > w["headroom_bytes"]["hbm"]:
                raise MemoryError("cache plus target physical HBM admission failed")
        self.result["cache_preflight"] = {"page_rounded_bytes": sum(self.cache_needed.values()),
                                          "location_bytes": self.cache_needed}
        if self.args.scene == "S5":
            evidence = ROOT / self.config["motivation_evidence"]
            artifact = json.loads(evidence.read_text())
            if (artifact.get("status") != "passed" or
                    not any(s.get("scenario") == "S5" and s.get("coverage") == "partial_verified_software_dependency"
                            for s in artifact.get("scenarios", [])) or
                    not artifact.get("minimum_shared_constraint", {}).get("verified_by")):
                raise ValueError("historical software-dependency audit must have passed")
            worker_source = ROOT / "main_lib/fluidckpt/cluster/worker.py"
            code = worker_source.read_text()
            if ('for op in run["plan"]["ops"]:' not in code or
                    'if self.rank not in (op["src_w"], op["dst_w"]): continue' not in code):
                raise ValueError("worker executor no longer has the audited global serial operation loop")
            self.result["software_gate"] = {"status": "qualified", "gate_kind": "software_worker_serial",
                "evidence_path": str(evidence), "evidence_sha256": digest(evidence),
                "runtime_path": str(worker_source), "runtime_sha256": digest(worker_source),
                "physical_resource_gate": "not_measured", "runtime_plan_dependency_required": True}
        self.save()

    def case(self, workers, replicas, targets):
        base = select_base(self.model, replicas)
        ids = set(self.owned)
        cache = [r for r in replicas if r["id"] in ids]
        return {"scenario_id": "S4", "workers": workers,
                "replicas": [dict(r, source_role="base", allocation_id=r["id"]) for r in base] +
                            [dict(r, source_role="extra", allocation_id=r["id"]) for r in cache],
                "targets": targets, "profile": self.cluster.profile, "executor": "serial",
                "format_contract": self.result["format_contract"], "requested_scene_id": self.args.scene,
                "assessment_scope": "S4 source/budget qualification only; S5 physical contention gate remains unmeasured",
                "budget": {"base_dram_bytes": sum(r.get("allocated_bytes", (r["n"]+4095)//4096*4096) for r in base),
                    "extra_hbm_bytes": 8*GIB, "location_extra_hbm_bytes": {f"{w}:hbm": 4*GIB for w in self.cache_needed},
                    "target_allocation_bytes": sum((s["n"]+4095)//4096*4096 for s in self.model["sources"]),
                    "staging_bytes": sum(w["staging_bytes"] for w in workers)}}

    def build_cache(self):
        started = time.monotonic()
        build = {"status": "running", "layout": self.layout, "flows": [], "outside_restore_window": True,
                 "extra_hbm_bytes": sum(self.cache_needed.values())}
        self.result["builds"].append(build)
        for i, row in enumerate(self.layout):
            rid = f"{self.prefix}/cache/{i}"
            self.owned.append(rid)
            self.result["owned_replica_ids"] = list(self.owned)
            self.save()
            t = target(self.model["sources"][row["tp"]], rid, row["worker"], off=row["off"], n=row["n"])
            flow = self.cluster.transfer(self.cluster.plan_restore([t], "resource_lp", self.model["source_ids"]))
            build["flows"].append(flow)
            if flow["plan"]["strategy"] != "resource_lp" or not flow["verification"]["passed"] or flow["verification"]["checked_bytes"] != row["n"]:
                raise AssertionError("cache build must verify every logical byte without solver fallback")
            self.save()
        workers, replicas = self.memory("cache_ready")
        actual = [r for r in replicas if r["id"] in set(self.owned)]
        if sum(r.get("allocated_bytes", (r["n"]+4095)//4096*4096) for r in actual) != 8*GIB:
            raise AssertionError("actual cache allocation differs from registered 8GiB")
        build.update(status="passed", wall_s=time.monotonic()-started, actual_replicas=actual)
        self.cache_signature = [(r["id"], r["worker"], r["off"], r["n"], r["generation"], r["epoch"]) for r in actual]
        self.save()

    def scheduled(self, plan):
        if self.args.scene == "S5":
            signature = control_signature(plan)
            if self.canonical_ops is None:
                self.canonical_ops = signature
            elif signature != self.canonical_ops:
                raise AssertionError("S5 planner changed canonical operation semantics")
            source_targets = {}
            for op in plan["ops"]:
                source_targets.setdefault(op["src_w"], set()).add(op["target"])
            if not any(len(ts) > 1 for ts in source_targets.values()):
                raise ValueError("S5 actual plan lacks a source worker shared across targets")
        return reordered_plan(plan, self.schedule)

    def restore(self, method, repetition):
        old_prefix = self.prefix
        if self.args.scene == "S5":
            self.prefix = old_prefix + "_" + self.schedule
        old_count = len(self.result["cases"])
        try:
            super().restore(method, repetition)
        finally:
            self.prefix = old_prefix
            if len(self.result["cases"]) > old_count:
                row = self.result["cases"][-1]
                row.update(schedule=self.schedule, subprotocol=self.result["subprotocol"],
                           scenario_variant=self.result["variant"], row_id=method+"_"+self.schedule,
                           comparison_group=self.out.name+"_fixed_layout", control_type="execution_order" if self.args.scene == "S5" else "routing_mechanism",
                           eligibility_contract="S4", physical_resource_gate="not_measured")
                if "flow" in row:
                    row["selected_source_bytes"] = selected_source_bytes(row["flow"]["plan"], row["assessment"]["resource_ledger"])
                    row["shared_cache_budget_bytes"] = 8*GIB
                    row["software_dependencies"] = dependency_report(row["flow"]["plan"])
                    row["controlled_operation_multiset"] = sorted(control_signature(row["flow"]["plan"]).elements())
                    row["control_comparison_scope"] = "same source/generation/range/path/op IDs and target rank/device; phase target key normalized"
                self.save()
                if self.args.scene == "S5" and row.get("status") == "passed":
                    try:
                        row["software_runtime_order_verification"] = verify_runtime_order(row["flow"]["plan"], row["flow"]["execution"])
                    except Exception as exc:
                        row.update(status="failed", error=repr(exc))
                        raise
                    finally:
                        self.save()

    def cleanup(self):
        super().cleanup()
        if hasattr(self, "before") and not self.owned:
            try:
                after, _ = self.cluster.inventory(compact=True)
                ledger = []
                for wid in sorted(self.cache_needed):
                    old, new = self.before[wid], after[wid]
                    row = {"worker": wid, "catalog_before": old["used_bytes"]["hbm"], "catalog_after": new["used_bytes"]["hbm"],
                           "allocated_delta": new["hbm_allocated_bytes"]-old["hbm_allocated_bytes"],
                           "reserved_delta": new["hbm_reserved_bytes"]-old["hbm_reserved_bytes"],
                           "physical_free_delta": new["headroom_bytes"]["hbm"]-old["headroom_bytes"]["hbm"]}
                    ledger.append(row)
                    if (row["catalog_after"] != row["catalog_before"] or
                            max(row["allocated_delta"], row["reserved_delta"]) > self.args.allocation_tolerance_mib << 20 or
                            row["physical_free_delta"] < -(self.args.physical_tolerance_mib << 20)):
                        raise AssertionError("cache worker allocator/catalog/free did not recover")
                self.result["cache_cleanup_physical_ledger"] = ledger
            except Exception as exc:
                self.result["cleanup_errors"].append(repr(exc))
        self.save()

    def run(self):
        self.result["status"] = "running"
        try:
            self.preflight()
            self.build_cache()
            for method in self.args.methods:
                for schedule in self.config["schedules"]:
                    self.schedule = schedule
                    for repetition in range(self.args.repetitions):
                        self.restore(method, repetition)
            self.result.update(status="passed", measurement_status="measured")
        except BaseException as exc:
            self.result.update(status="failed", error=repr(exc), traceback=traceback.format_exc())
        finally:
            self.cleanup()
            if self.result["cleanup_errors"]:
                self.result["status"] = "failed"
            self.save()
        return self.result["status"] == "passed"


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scene", choices=("S4", "S5"))
    parser.add_argument("--register-protocol", action="store_true")
    parser.add_argument("--reverse-schedules", action="store_true", help="S5 only: run rank_interleaved before target_major")
    parser.add_argument("--target-slot")
    parser.add_argument("--cache-slot")
    parser.add_argument("--methods", nargs="+", choices=("janus_host", "fastest", "resource_lp"))
    parser.add_argument("--source-slot", default="target_a")
    parser.add_argument("--repetitions", type=int, choices=(1, 2), default=1)
    parser.add_argument("--guard-mib", type=int, default=2048)
    parser.add_argument("--allocation-tolerance-mib", type=int, default=32)
    parser.add_argument("--physical-tolerance-mib", type=int, default=256)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.reverse_schedules and args.scene != "S5":
        parser.error("--reverse-schedules is only valid for S5")
    protocol = json.loads(PROTOCOL.read_text())
    if args.register_protocol:
        if args.execute or args.scene:
            parser.error("protocol registration is CPU-only and separate from scenario execution")
        state = load_state(args.session)
        layouts = {s: cache_layout(state["models"]["B"], state["slots"][protocol[s]["cache_slot"]], protocol[s])[0] for s in ("S4", "S5")}
        out = new_run(args.out)
        write(out / "summary.json", {"status": "registered_only", "measurement_status": "not_measured",
            "protocol": protocol, "protocol_sha256": digest(PROTOCOL), "session_sha256": digest(args.session),
            "planned_layouts": layouts, "new_gpu_restore_count": 0})
        print(json.dumps({"status": "registered_only", "out": str(out)}))
        return
    if not args.scene:
        parser.error("--scene or --register-protocol is required")
    config = protocol[args.scene]
    args.target_slot = args.target_slot or config["target_slot"]
    args.cache_slot = args.cache_slot or config["cache_slot"]
    args.methods = args.methods or config["methods"]
    if len(set(args.methods)) != len(args.methods) or any(getattr(args, k) < 0 for k in ("guard_mib", "allocation_tolerance_mib", "physical_tolerance_mib")):
        parser.error("duplicate methods or negative tolerances")
    if args.scene == "S5" and args.methods != ["fastest"]:
        parser.error("S5 freezes fastest routing; only schedule order may vary")
    runner = LayoutScenarioRun(args)
    if args.execute and not runner.run():
        raise SystemExit(1)
    if not args.execute:
        print(json.dumps({"status": "registered_only", "out": str(runner.out)}))


if __name__ == "__main__":
    main()
