"""Qualify two fresh HIP IPC imports on an existing parked Qwen or GLM engine.

This driver never starts or stops services. Only its own HBM replicas may be
retired. A failed consumer release leaves those replicas intact for diagnosis.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import resource
import shlex
import sys
import time
import traceback
import urllib.request

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "main_lib/fluidckpt").is_dir())
sys.path.insert(0, str(ROOT / "main_lib"))
sys.path.insert(0, str(ROOT / "codex_coding/src/task2/cluster"))
from fluidckpt.cluster.control import Cluster, request
from fluidckpt.serving.measure import summarize
from run_lifecycle import LifecycleRun
from run_system import SystemRun


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def protected_state(workers, own_ids=()):
    """Compare original replicas, epochs and leases without transient headroom."""
    excluded = set(own_ids)
    return [{"worker": w["worker"], "epoch": w["epoch"], "session": w["session"],
             "replicas": sorted([r for r in w["replicas"] if r["id"] not in excluded],
                                key=lambda r: r["id"])} for w in workers]


def check_source_coverage(model, replicas, allowed_tiers=("dram",)):
    ids = set(model["source_ids"])
    selected = [r for r in replicas if r["id"] in ids]
    if {r["id"] for r in selected} != ids:
        raise ValueError("session base source IDs no longer match the live VALID catalog")
    if any(r["tier"] not in allowed_tiers for r in selected):
        raise ValueError(f"base source tiers must belong to {allowed_tiers}")
    for source in model["sources"]:
        end = 0
        for r in sorted((r for r in selected if r["tp"] == source["tp"]), key=lambda r: r["off"]):
            if r["identity"] != source["identity"] or r["off"] > end:
                raise ValueError("source identity mismatch or incomplete byte coverage")
            end = max(end, r["off"] + r["n"])
        if end != source["n"]:
            raise ValueError("source coverage length differs from the captured rank")
    return selected


def release_evidence(directory, ranks):
    result = {}
    for rank in range(ranks):
        path = Path(directory) / f"rank{rank}.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        state_events = [r for r in events if r["event"] in
                        ("load_begin", "weights_ready", "release_begin", "release_end", "load_failed")]
        if not state_events or state_events[-1]["event"] != "release_end":
            raise ValueError(f"rank {rank} lacks completed parked-state evidence")
        result[str(rank)] = {"path": str(path), "sha256": digest(path), "last_event": state_events[-1]}
    return result


def load_timing(directory, ranks):
    """Keep host clocks separate; coordinator RPC timing is recorded by caller."""
    by_host = {}
    for rank in range(ranks):
        events = [json.loads(line) for line in (Path(directory) / f"rank{rank}.jsonl").read_text().splitlines()]
        starts = [r for r in events if r["event"] == "load_begin"]
        ends = [r for r in events if r["event"] == "weights_ready"]
        if len(starts) != 1 or len(ends) != 1 or any(r["event"] == "load_failed" for r in events):
            raise ValueError(f"rank {rank} loading boundaries are invalid")
        begin, end = starts[0], ends[0]
        if len({r["host"] for r in events}) != 1 or end["monotonic_ns"] < begin["monotonic_ns"]:
            raise ValueError(f"rank {rank} contains invalid clock boundaries")
        by_host.setdefault(begin["host"], {})[str(rank)] = {
            "begin_ns": begin["monotonic_ns"], "end_ns": end["monotonic_ns"],
            "rank_s": (end["monotonic_ns"] - begin["monotonic_ns"]) / 1e9, "events": events}
    host_windows = {host: {"load_s": (max(r["end_ns"] for r in rows.values()) -
                        min(r["begin_ns"] for r in rows.values())) / 1e9, "ranks": rows}
                    for host, rows in by_host.items()}
    return {"per_host": host_windows, "same_host": len(by_host) == 1,
            "primary_T_ready_s": summarize(directory, ranks)["load_s"] if len(by_host) == 1 else None,
            "cross_host_policy": "no cross-host subtraction; use separately named coordinator ready acknowledgment"}


class IpcGate:
    generate_stream = LifecycleRun.generate_stream
    replica_target = SystemRun.replica_target

    def __init__(self, args):
        self.args = args
        self.out = args.out.resolve()
        self.out.mkdir(parents=True, exist_ok=False)
        os.chmod(self.out, 0o777)
        self.state = json.loads(args.session.read_text())
        root_path = Path(self.state["root_session"])
        root = json.loads(root_path.read_text())
        self.slot = self.state["slots"][args.model]
        self.model = self.state["models"][args.model]
        self.ranks = self.model["tp"]
        self.allowed_source_tiers = ("dram", "ssd") if args.model == "A" else ("dram",)
        self.endpoints = root["endpoints"]
        self.cluster = Cluster(self.endpoints, self.state["profile"], planner_endpoint=self.endpoints[0])
        self.own_ids = []
        self.consumer_may_hold_refs = False
        self.parked_verified = False
        self.old_command = None
        self.result = {"development": "v0.1.23.3", "task": "task2", "experiment_id": "e1",
                       "case_id": f"ipc_{args.model}_gate", "model": args.model,
                       "run_id": self.out.name, "status": "registered",
                       "hits": [], "cleanup_errors": [], "memory_samples": []}
        files = sorted((ROOT / "main_lib/fluidckpt").rglob("*.py"))
        files += [Path(__file__), ROOT / "codex_coding/src/task2/cluster/run_lifecycle.py",
                  ROOT / "codex_coding/src/task2/cluster/run_system.py",
                  ROOT / "codex_coding/src/task2/cluster/glm_ipc_baseline.py"]
        self.hashes = {str(p.relative_to(ROOT)): digest(p) for p in files}
        evidence = json.loads(args.runtime_evidence.read_text()) if args.runtime_evidence else None
        self.result["runtime_evidence"] = evidence
        protocol = {"development": "v0.1.23.3", "task": "task2", "experiment_id": "e1",
            "run_id": self.out.name, "repetitions": 2,
            "scope": "adapted HIP IPC resident HBM; existing eager warm engine",
            "model": args.model, "tp": self.ranks,
            "session": {"path": str(args.session.resolve()), "sha256": digest(args.session)},
            "root_session": {"path": str(root_path), "sha256": digest(root_path)},
            "source_sha256": self.hashes, "source_hash_scope": "disk sources; live loaded code requires separate evidence",
            "primary_metric": "same-host max(weights_ready)-min(load_begin)",
            "coordinator_metric": "fresh export start through resume RPC acknowledgment; includes RPC/confirmation",
            "cross_host_policy": "per-host local windows plus coordinator ready acknowledgment; no cross-host subtraction",
            "preparation_policy": "owner build and full transfer SHA outside hit; fresh export inside request total",
            "correctness": "full owner-build transfer SHA and exact 32-token deterministic reference text per hit",
            "base_source_tiers_allowed": self.allowed_source_tiers,
            "base_source_invariant_scope": "preserved DRAM/SSD catalog metadata, owner epochs and source IDs; not a post-run byte rehash",
            "release_tolerance_bytes": args.allocation_tolerance_mib << 20,
            "physical_free_tolerance_bytes": args.physical_tolerance_mib << 20,
            "hbm_guard_bytes": args.guard_mib << 20,
            "cleanup": "only newly created HBM IDs; never retire while consumer release is uncertain",
            "parked_check": "session flag + latest per-rank release_end + source/target admission + read-only get_model_info",
            "health_endpoint": "prohibited on parked engine: /health may execute a forward pass",
            "runtime_evidence": evidence}
        write(self.out / "protocol.json", protocol)
        (self.out / "commands.txt").write_text(shlex.join([sys.executable, *sys.argv]) + "\n")
        self.save()

    def save(self):
        write(self.out / "summary.json", self.result)

    def event(self, name, **fields):
        row = {"event": name, "wall_time": time.time(), **fields}
        with (self.out / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(row) + "\n")
        self.save()
        print(json.dumps(row), flush=True)

    def memory(self, name):
        workers, replicas = self.cluster.inventory(compact=True)
        write(self.out / f"inventory_{name}.json", workers)
        self.result["memory_samples"].append({"label": name, "workers": [
            {k: w[k] for k in ("worker", "node", "gpu", "hbm_allocated_bytes", "hbm_reserved_bytes",
                               "headroom_bytes", "hbm_total_bytes", "used_bytes", "epoch")}
            for w in workers]})
        self.save()
        return workers, replicas

    def preflight(self):
        if self.ranks not in (2, 16) or len(self.slot["workers"]) != self.ranks or not self.slot["parked"]:
            raise ValueError("requires a parked Qwen TP2 or GLM TP16 session overlay")
        evidence = self.result["runtime_evidence"]
        if evidence and evidence.get("known_loaded_source_mismatch"):
            raise ValueError("live loaded source mismatch; rebuild externally before this gate")
        self.result["engine_info"] = request(self.slot["endpoint"], "get_model_info", timeout=10)
        command_path = Path(self.slot["command"])
        self.old_command = command_path.read_bytes()
        old = json.loads(self.old_command)
        write(self.out / "command_before.json", old)
        self.result["initial_parked_evidence"] = release_evidence(old["metrics"], self.ranks)
        self.parked_verified = True
        before, replicas = self.memory("before")
        self.before = before
        if any(w["transactions"] for w in before):
            raise ValueError("shared cluster has active transactions")
        self.source_rows = check_source_coverage(self.model, replicas, self.allowed_source_tiers)
        write(self.out / "base_sources_before.json", self.source_rows)
        self.result["base_source_bytes_by_tier"] = {tier: sum(r["n"] for r in self.source_rows if r["tier"] == tier)
                                                   for tier in self.allowed_source_tiers}
        self.result["base_source_logical_model_bytes"] = sum(s["n"] for s in self.model["sources"])
        target_nodes = {before[w]["node"] for w in self.slot["workers"]}
        if self.ranks == 2 and len(target_nodes) != 1:
            raise ValueError("Qwen gate primary metric requires a single target host")
        for source, wid in zip(self.model["sources"], self.slot["workers"]):
            worker = before[wid]
            needed = (source["n"] + 4095) // 4096 * 4096
            if worker["used_bytes"]["hbm"] + needed > worker["budget_bytes"]["hbm"]:
                raise MemoryError("owner HBM logical budget admission failed")
            if worker["headroom_bytes"]["hbm"] < needed + (self.args.guard_mib << 20):
                raise MemoryError("owner HBM physical headroom admission failed")
        self.event("preflight_passed", source_bytes=sum(s["n"] for s in self.model["sources"]))

    def build_owner(self):
        self.targets = [self.replica_target(s, f"paper/{self.out.name}/ipc/rank{rank}", wid)
                        for rank, (s, wid) in enumerate(zip(self.model["sources"], self.slot["workers"]))]
        self.own_ids = [t["id"] for t in self.targets]
        self.result["owned_replica_ids"] = self.own_ids
        self.save()
        started = time.monotonic()
        plan = self.cluster.plan_restore(self.targets, "resource_lp", self.model["source_ids"])
        write(self.out / "owner_plan.json", plan)
        flow = self.cluster.transfer(plan)
        self.result["owner_setup_request_s"] = time.monotonic() - started
        write(self.out / "owner_flow.json", flow)
        self.result["owner_setup"] = {k: flow[k] for k in
            ("tx", "prepare_s", "execute_wall_s", "verify_and_publish_s", "coordinator_wall_s", "verification")}
        self.result["owner_bytes"] = sum(s["n"] for s in self.model["sources"])
        source_tiers = {r["id"]: r["tier"] for r in self.source_rows}
        self.result["owner_build_source_bytes_by_tier"] = {
            tier: sum(n for rid, n in flow["source_bytes"].items() if source_tiers[rid] == tier)
            for tier in self.allowed_source_tiers}
        self.memory("owner_ready")
        self.event("owner_built", seconds=self.result["owner_setup_request_s"])

    def cycle(self, index):
        phase = f"{self.out.name}_ipc_hit{index}"
        metrics = self.out / "metrics" / phase
        directory = self.out / "ipc" / phase
        row = {"repetition": index, "phase": phase, "status": "started"}
        self.result["hits"].append(row)
        self.save()
        write(Path(self.slot["command"]), {"tag": phase, "mode": "ipc", "metrics": str(metrics),
                                           "ipc_directory": str(directory)})
        started = time.monotonic()
        with ThreadPoolExecutor(self.ranks) as pool:
            exports = list(pool.map(lambda t: request(self.endpoints[t["worker"]], "export_ipc",
                {"id": t["id"], "directory": str(directory)}, timeout=300), self.targets))
        row["export_s"] = time.monotonic() - started
        row["exports"] = exports
        row["handle_files_sha256"] = {str(p): digest(p) for p in directory.glob("rank*.pkl")}
        self.consumer_may_hold_refs = True
        self.parked_verified = False
        resume_start = time.monotonic()
        request(self.slot["endpoint"], "resume_memory_occupation", {"tags": ["weights"]}, timeout=600)
        row["resume_rpc_s"] = time.monotonic() - resume_start
        row["request_to_ready_ack_s"] = time.monotonic() - started
        row["request_to_weights_ready_s"] = row["request_to_ready_ack_s"]
        row["request_to_weights_ready_scope"] = "coordinator start to all-rank-barrier resume RPC acknowledgment"
        row["timing"] = load_timing(metrics, self.ranks)
        row["primary_T_ready_s"] = row["timing"]["primary_T_ready_s"]
        self.save()
        reply = self.generate_stream(self.slot)
        row.update(reply=reply, correct=reply["text"] == self.model["reference"]["text"],
                   request_to_first_token_s=reply["first_token_time"] - started,
                   request_to_generation_done_s=time.monotonic() - started)
        self.memory(f"hit{index}_consumer_attached")
        release_start = time.monotonic()
        request(self.slot["endpoint"], "release_memory_occupation", {"tags": ["weights"]}, timeout=300)
        self.consumer_may_hold_refs = False
        self.parked_verified = True
        row["release_s"] = time.monotonic() - release_start
        row["release_evidence"] = release_evidence(metrics, self.ranks)
        self.memory(f"hit{index}_consumer_released")
        row["status"] = "passed" if row["correct"] else "failed"
        self.event("ipc_cycle_complete", repetition=index, correct=row["correct"],
                   primary_T_ready_s=row["primary_T_ready_s"], export_s=row["export_s"])
        if not row["correct"]:
            raise AssertionError("IPC output differs from captured deterministic reference")

    def cleanup(self):
        if self.consumer_may_hold_refs:
            try:
                request(self.slot["endpoint"], "release_memory_occupation", {"tags": ["weights"]}, timeout=300)
                self.consumer_may_hold_refs = False
                self.parked_verified = True
            except Exception as exc:
                self.result["cleanup_errors"].append("consumer release failed; keep own HBM alive: " + repr(exc))
        if self.own_ids and not self.consumer_may_hold_refs:
            try:
                _, replicas = self.cluster.inventory(compact=True)
                existing = {r["id"] for r in replicas}
                for rid in self.own_ids:
                    if rid in existing:
                        self.cluster.retire(rid)
            except Exception as exc:
                self.result["cleanup_errors"].append("own HBM retirement failed: " + repr(exc))
        if hasattr(self, "before"):
            try:
                after, replicas = self.memory("after")
                unchanged = protected_state(self.before) == protected_state(after, self.own_ids)
                self.result["protected_catalog_unchanged"] = unchanged
                base_after = [r for r in replicas if r["id"] in set(self.model["source_ids"])]
                write(self.out / "base_sources_after.json", base_after)
                self.result["base_sources_unchanged"] = (sorted(base_after, key=lambda r: r["id"]) == sorted(
                    self.source_rows, key=lambda r: r["id"])) if hasattr(self, "source_rows") else None
                if not unchanged or self.result["base_sources_unchanged"] is False:
                    raise AssertionError("preserved catalog or base source metadata changed")
                if self.own_ids:
                    self.result["owner_retirement"] = [{"worker": wid,
                        "allocated_before": self.before[wid]["hbm_allocated_bytes"],
                        "allocated_after": after[wid]["hbm_allocated_bytes"],
                        "reserved_before": self.before[wid]["hbm_reserved_bytes"],
                        "reserved_after": after[wid]["hbm_reserved_bytes"],
                        "physical_free_before": self.before[wid]["headroom_bytes"]["hbm"],
                        "physical_free_after": after[wid]["headroom_bytes"]["hbm"],
                        "catalog_used_before": self.before[wid]["used_bytes"]["hbm"],
                        "catalog_used_after": after[wid]["used_bytes"]["hbm"]}
                        for wid in self.slot["workers"]]
                    for r in self.result["owner_retirement"]:
                        if r["allocated_after"] > r["allocated_before"] + (self.args.allocation_tolerance_mib << 20):
                            raise AssertionError("owner allocated memory remains after retirement")
                        if r["reserved_after"] > r["reserved_before"] + (self.args.allocation_tolerance_mib << 20):
                            raise AssertionError("owner reserved memory remains after retirement")
                        if r["physical_free_after"] < r["physical_free_before"] - (self.args.physical_tolerance_mib << 20):
                            raise AssertionError("physical free HBM did not recover within registered tolerance")
                        if r["catalog_used_after"] != r["catalog_used_before"]:
                            raise AssertionError("owner HBM catalog charge did not recover")
            except Exception as exc:
                self.result["cleanup_errors"].append(repr(exc))
        self.result["source_hash_mismatches"] = [p for p, h in self.hashes.items() if digest(ROOT / p) != h]
        if self.result["source_hash_mismatches"]:
            self.result["cleanup_errors"].append("recorded source files changed during run")
        self.result["final_engine_state"] = ("unknown; own owner retained" if self.consumer_may_hold_refs
            else "parked" if self.parked_verified else "not verified")
        self.save()

    def run(self):
        self.result["status"] = "running"
        try:
            self.preflight()
            self.build_owner()
            for index in range(2):
                self.cycle(index)
            self.result["status"] = "passed"
        except BaseException as exc:
            self.result.update(status="failed", error=repr(exc), traceback=traceback.format_exc())
        finally:
            self.cleanup()
            if self.result["cleanup_errors"]:
                self.result["status"] = "failed"
            self.event("run_complete", status=self.result["status"], cleanup_errors=self.result["cleanup_errors"])
        return self.result["status"] == "passed"


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--model", choices=("A", "B"), default="B")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runtime-evidence", type=Path)
    parser.add_argument("--guard-mib", type=int, default=1024)
    parser.add_argument("--allocation-tolerance-mib", type=int, default=32)
    parser.add_argument("--physical-tolerance-mib", type=int, default=256)
    parser.add_argument("--execute", action="store_true", help="Run the registered GPU gate; otherwise register only")
    args = parser.parse_args()
    gate = IpcGate(args)
    if args.execute and not gate.run():
        raise SystemExit(1)
    if not args.execute:
        print(json.dumps({"status": "registered_only", "out": str(gate.out)}))


if __name__ == "__main__":
    main()
