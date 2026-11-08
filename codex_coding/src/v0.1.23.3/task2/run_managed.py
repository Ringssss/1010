"""Validate the managed control path on small fixtures and one complete Qwen TP2 restore."""
import argparse
import copy
import json
from pathlib import Path
import resource
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT/"codex_coding/src/task2/cluster"))
from run_placement_comparison import PlacementRun
from fluidckpt.cluster.control import request
from fluidckpt.cluster.managed import rows, state_hash
from fluidckpt.cluster.managed_control import ManagedPlacementError
from fluidckpt.serving.measure import summarize

GIB = 1 << 30


class ManagedRun(PlacementRun):
    source_workers = [4, 5]
    target_workers = [6, 7]
    replica_workers = [2, 3]
    target_node = "node29"

    def preflight(self):
        from fluidckpt.memory.bindings import choose_bindings
        super().preflight()
        if self.args.fixture_only: return
        # This runner validates the fixed Qwen snapshot. The allocator still checks actual sizes.
        required = getattr(self.args, "rank_reserve_bytes", 35164859056)
        demands = {}
        for w in sorted(set([0, 1]+self.source_workers)):
            node, gpu = self.members[w]
            demands.setdefault(node, []).append({"gpu": gpu, "nbytes": required})
        decisions = {node: choose_bindings(self.probes[node]["topology"], ds) for node, ds in demands.items()}
        self.args.worker_numas = {f"{node}:{r['gpu']}": r["numa"] for node, decision in decisions.items() for r in decision["bindings"]}
        self.result["initial_memory_bindings"] = decisions
        self.event("initial_memory_bindings_ready", bindings=self.args.worker_numas, required_rank_bytes=required)

    def __init__(self, args):
        super().__init__(args)
        path = getattr(args, "network_pairs_file", None)
        if path:
            report = json.loads(Path(path).read_text())
            if not report["passed"] or report["members"] != [list(m) for m in self.members]:
                raise ValueError("network path evidence does not match this mesh")
            self.cluster.profile = dict(copy.deepcopy(self.cluster.profile), network_pairs=report["network_pairs"],
                                        network_pairs_provenance=str(path))
            self.result["network_path_evidence"] = report

    def demand(self, sources, scope, base_ids, managed_ids, block, hbm, dram, reserve=0):
        locations = [{"worker": w, "tier": "hbm", "max_bytes": hbm,
                      "reserve_free_bytes": reserve if w in self.target_workers else 2*GIB}
                     for w in sorted(set(self.replica_workers+self.target_workers))]
        locations += [{"worker": w, "tier": "dram", "max_bytes": dram, "reserve_free_bytes": 2*GIB} for w in self.target_workers]
        return {"schema_version": 1, "scope": scope, "executor": "serial", "block_bytes": block,
                "base_ids": base_ids, "managed_ids": managed_ids,
                "targets": [{"key": f"{scope}/{g}", "identity": s["identity"], "tp": s["tp"],
                             "worker": self.target_workers[g], "off": 0, "n": s["n"]} for g, s in enumerate(sources)],
                "budget": {"tier_bytes": {"hbm": hbm, "dram": dram},
                           "temporary_tier_bytes": {"hbm": 0, "dram": 0}, "locations": locations}}

    def fixture(self):
        if len({node for node, gpu in self.members}) > 1 and "network_pairs" not in self.cluster.profile:
            raise ValueError("cross-node validation needs --network-pairs-file from run_path_probe.py")
        self.cluster.profile = dict(copy.deepcopy(self.cluster.profile), serial_target_execution=True,
                                    provenance="functional fixture estimates; not hardware calibration")
        sources = []
        for rank in range(2):
            request(self.endpoints[self.source_workers[rank]], "synth", {"snap": "managed_fixture", "tp": rank,
                    "nbytes": (96 << 20)+17, "seed": 9200+rank})
            sources.append(request(self.endpoints[self.source_workers[rank]], "adopt",
                {"snap": "managed_fixture", "tp": rank, "id": f"mf/base/{rank}"}))
        initial = [self.replica_target(s, f"mf/old/{g}", self.replica_workers[g], "hbm", n=32 << 20) for g, s in enumerate(sources)]
        self.cluster.transfer(self.cluster.plan_restore(initial, "resource_lp", [s["id"] for s in sources]))
        q = self.demand(sources, "fixture", [s["id"] for s in sources], [t["id"] for t in initial],
                        32 << 20, 64 << 20, 0)
        q["minimum_gain_s"] = 1e-6
        plan = self.cluster.plan_joint(q)
        stale = self.cluster.plan_joint(dict(q, scope="fixture_stale"))
        if not plan["transition"]["actions"]: raise AssertionError("fixture requires a real transition")
        journal_path = self.out/"fixture_journal.json"
        try:
            self.cluster.apply_joint(plan, journal_path=journal_path, fail_after_actions=1)
            raise AssertionError("injected interruption was not raised")
        except ManagedPlacementError as exc:
            if "injected interruption" not in str(exc): raise
            interrupted = exc.journal
        workers, replicas = self.cluster.inventory(compact=True)
        if any(w["transactions"] for w in workers) or any(r["leases"] for r in replicas):
            raise AssertionError("checkpoint interruption retained active transfer")
        resumed = self.cluster.apply_joint(plan, journal_path=journal_path)
        repeated = self.cluster.apply_joint(plan, journal_path=journal_path)
        if repeated["completed"] != resumed["completed"]: raise AssertionError("reapply repeated actions")
        before = state_hash(self.cluster.inventory(compact=True)[0])
        try:
            self.cluster.apply_joint(stale)
            raise AssertionError("stale plan accepted")
        except ManagedPlacementError as exc:
            if "stale" not in str(exc): raise
        if before != state_hash(self.cluster.inventory(compact=True)[0]): raise AssertionError("stale rejection changed storage")
        targets = [self.replica_target(s, f"mf/restored/{g}", self.target_workers[g]) for g, s in enumerate(sources)]
        restored = self.cluster.transfer(self.cluster.plan_managed_restore(plan, targets))
        q["managed_ids"] = [r["id"] for r in plan["selected"]]
        # Remove only the fixture output allocations before checking planning stability.
        for t in targets: self.cluster.retire(t["id"])
        again = self.cluster.plan_joint(q)
        if again["decision"] != "keep_current" or again["transition"]["actions"]:
            raise AssertionError("already optimal layout replanned unnecessary movement")
        self.result["managed_fixture"] = {"passed": True, "plan": plan, "interrupted": interrupted,
            "resumed": resumed, "stale_rejected": True, "idempotent": True,
            "restored": restored, "repeat_decision": again["decision"]}
        _, replicas = self.cluster.inventory()
        for r in replicas: request(self.endpoints[r["worker"]], "retire", {"id": r["id"]})
        workers, replicas = self.cluster.inventory()
        if replicas or any(any(w["used_bytes"].values()) or w["transactions"] for w in workers):
            raise AssertionError("fixture resources remain")
        self.event("managed_fixture_passed", actions=len(plan["transition"]["actions"]),
                   verified_bytes=restored["verification"]["checked_bytes"])

    def restore_managed(self, placement, sources, reference):
        phase = "managed_restore"
        server = self.server(self.target_node, phase, "restore")
        self.wait_exports(phase)
        targets = [dict(self.replica_target(s, f"{phase}/{g}", self.target_workers[g]), mode="live", tag=phase,
                        metrics_directory=str(self.out/"metrics"/phase)) for g, s in enumerate(sources)]
        stamp = time.monotonic()
        plan = self.cluster.plan_managed_restore(placement, targets)
        planned = time.monotonic()
        if plan["strategy"] != "resource_lp" or plan["fallback_reason"]: raise AssertionError("restore LP fallback")
        flow = self.cluster.transfer(plan, verify_before_ready=False, retain=True)
        self.event("weights_transferred", phase=phase, control_s=time.monotonic()-stamp, source_bytes=flow["source_bytes"])
        replies = self.generate(self.target_node)
        if [r["text"] for r in replies] != [r["text"] for r in reference]: raise AssertionError("generation mismatch")
        verified = self.cluster.verify(flow["tx"])
        timing = summarize(self.out/"metrics"/phase, 2)
        self.cluster.finish(flow["tx"]); self.stop(server)
        self.result["restores"].append({"phase": phase, "node": self.target_node, "strategy": "resource_lp", "flow": flow,
            "verification": verified, "replies": replies, "timing": timing, "passed": True, "plan_wall_s": planned-stamp})
        self.event("managed_model_passed", load_s=timing["load_s"], verified_bytes=verified["checked_bytes"])

    def calibrate(self, sources):
        local = {}
        for tier, prefix in (("dram", "model/joint"), ("hbm", "calibration/hbm")):
            targets = [self.replica_target(s, f"{prefix}/{g}", self.target_workers[g], tier, GIB+17, GIB)
                       for g, s in enumerate(sources)]
            self.cluster.transfer(self.cluster.plan_restore(targets, "nearest", [f"model/remote/{g}" for g in range(2)]))
            local[tier] = targets
        observations = []
        variants = [("dram", [f"model/remote/{g}" for g in range(2)]),
                    ("hbm", [f"model/gpu/{g}" for g in range(2)]),
                    ("dram", [t["id"] for t in local["dram"]]), ("hbm", [t["id"] for t in local["hbm"]])]
        for variant, (tier, ids) in enumerate(variants):
            for label, n in (("warm", 32 << 20), ("measure", GIB)):
                targets = [self.replica_target(s, f"mc/{variant}/{label}/{g}", self.target_workers[g], "hbm", GIB+17, n)
                           for g, s in enumerate(sources)]
                flow = self.cluster.transfer(self.cluster.plan_restore(targets, "nearest", ids))
                if label == "measure":
                    kinds = {op["kind"] for op in flow["plan"]["ops"]}
                    if len(kinds) != 1: raise AssertionError("calibration pairs use different path classes")
                    name = kinds.pop()+":"+tier
                    times = [sum(o["elapsed_s"] for o in w["records"] if o["role"] in ("target", "both"))
                             for w in flow["execution"] if w["worker"] in self.target_workers]
                    observations.append({"path": name, "rank_bytes": n, "target_seconds": times, "GBps": n/1e9/max(times), "flow": flow})
                    self.event("path_calibrated", path=name, rank_seconds=times)
                for target in targets: self.cluster.retire(target["id"])
        for target in local["hbm"]: self.cluster.retire(target["id"])
        rates = {r["path"]: r["GBps"] for r in observations}
        profile = dict(copy.deepcopy(self.cluster.profile), path_rates=rates, serial_target_execution=True,
                       provenance="one current-hardware 1GiB sample per path, two ranks; contiguous targets, provisional")
        if "network:dram" in rates: profile["network_node_GBps"] = 2*rates["network:dram"]
        self.cluster.profile = profile
        self.result.update(calibration=observations, comparison_profile=profile)
        self.save()

    def real_model(self):
        sources, reference = self.prepare_model()
        self.calibrate(sources)
        workers, replicas = self.cluster.inventory(compact=True)
        base = [r["id"] for r in replicas if r["id"].startswith("model/remote/")]
        managed = [r["id"] for r in replicas if r["id"] not in base]
        q = self.demand(sources, "qwen_tp2", base, managed, GIB, 8*GIB, 2*GIB, reserve=40*GIB)
        placement = self.cluster.plan_joint(q)
        (self.out/"placement_plan.json").write_text(json.dumps(placement, indent=2))
        self.result["managed_request"] = q
        self.result["managed_plan"] = placement
        self.event("joint_plan_ready", decision=placement["decision"], candidates=placement["candidates"]["count"],
                   solver=placement["solver"]["status"], before_s=placement["predicted_before_s"], after_s=placement["predicted_after_s"])
        if placement["fallback_reason"]: raise AssertionError("real placement unexpectedly fell back")
        stamp = time.monotonic()
        applied = self.cluster.apply_joint(placement, journal_path=self.out/"model_journal.json")
        self.result["managed_apply"] = {"wall_s": time.monotonic()-stamp, "journal": applied}
        self.result["published_layout"] = self.layout()
        self.event("managed_layout_applied", actions=len(applied["completed"]), usage=applied["final_usage"])
        self.restore_managed(placement, sources, reference)
        self.result["scope"] = "automated full-manifest candidates, joint RPC, bounded transition and one model restore; not a matched speedup benchmark"
        self.result["validation_topology"] = {"members": self.members, "source_workers": self.source_workers,
            "target_workers": self.target_workers, "target_node": self.target_node,
            "cross_node_validated": len({node for node, gpu in self.members}) > 1}
        self.result["stages"]["managed_control"] = {"passed": True, "model_restores": 1}


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(); parser.add_argument("--run", required=True)
    parser.add_argument("--fixture-only", action="store_true")
    parser.add_argument("--port-base", type=int, default=46100)
    parser.add_argument("--model", default="/data/node28/shared/zhujianian/Qwen3.6-35B-A3B")
    parser.add_argument("--network-pairs-file")
    parser.add_argument("--rank-reserve-bytes", type=int, default=35164859056)
    parser.set_defaults(worker_serialize=0, handoff_poll=.01, development="v0.1.23.3",
        result_root=str(ROOT/"codex_coding/results/v0.1.23.3/task2"), source_directories=[str(Path(__file__).parent)])
    ManagedRun(parser.parse_args()).run()
