"""Warm Qwen S2/S3 mechanism comparisons on explicitly supplied fresh engines.

Bootstrap is a separate existing restore_engine.py operation. This runner never
starts or stops engines, keepers or containers. Registration does not use GPUs.
"""
import argparse
from concurrent.futures import Future, ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import resource
import shlex
import sys
import threading
import time
import traceback
from runtime_support import (ROOT, bootstrap, cleanup_bootstrap, code_freeze, digest, format_contract, generate, http,
                             load_state, new_run, select_base, source_signature, target, write)

sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.benchmarks.basic_scenarios import assess_case, method_catalog, scenario_contract
from fluidckpt.cluster.control import Cluster
from fluidckpt.serving.handoff import wait_for_exports
from fluidckpt.serving.measure import summarize


class ScenarioRun:
    def __init__(self, args):
        self.args = args
        self.out = new_run(args.out)
        os.chmod(self.out, 0o777)
        self.state = load_state(args.session)
        self.model = self.state["models"]["B"]
        if self.model["tp"] != 2:
            raise ValueError("basic runner requires the complete Qwen TP2 processed snapshot")
        self.target_slot = copy.deepcopy(self.state["slots"][args.target_slot])
        self.source_slot = copy.deepcopy(self.state["slots"][args.source_slot]) if args.scene == "S3" else None
        self.cluster = Cluster(self.state["endpoints"], self.state["profile"], executor="serial")
        self.prefix = "basic_" + digest(args.session)[:8] + "_" + self.out.name
        self.owned = []
        self.active_instances = []
        self.modified_commands = {}
        self.unreleased_slots = {}
        self.resume_futures = {}
        self.command_keep = set()
        self.freeze = code_freeze()
        catalog = method_catalog()
        self.result = {"status": "registered", "measurement_status": "not_measured",
                       "scene_id": args.scene, "scenario_id": args.scene, "run_id": self.out.name,
                       "development": "v0.1.23.3", "task": "task2", "experiment_id": "e1", "gate_kind": "capability_gate", "cases": [], "cleanup_errors": [],
                       "methods": {m: catalog[m] for m in args.methods},
                       "adaptation_identity": "common_executor_mechanism_comparison",
                       "frozen_upstream_commits": {r["name"]: r["commit"] for r in json.loads(
                           (ROOT / "lib_cite/baselines.lock.json").read_text())["repositories"]},
                       "code_sha256": self.freeze, "session_sha256": digest(args.session),
                       "format_contract": format_contract(self.model), "memory_samples": [],
                       "protocol": {"scene": scenario_contract(args.scene), "repetitions": args.repetitions,
                           "source_availability": "S2 exact complete DRAM; S3 same DRAM plus one actual active IPC-backed logical TP2 instance",
                           "primary_metric": "same-host max(weights_ready)-min(load_begin)",
                           "request_metric": "coordinator request through all-rank resume acknowledgment",
                           "verification": "full transfer SHA and deterministic exact 32-token text after readiness",
                           "build_cost": "source HBM build/IPC attach outside restore; recorded separately",
                           "cleanup": "only this run owner replicas; require consumer detach before retirement",
                           "foreground_scope": "active source survives copy; no sustained source traffic benchmark",
                           "physical_peak_verified": False}}
        write(self.out / "commands.txt.json", {"argv": [sys.executable, *sys.argv], "shell": shlex.join([sys.executable, *sys.argv])})
        self.save()

    def save(self):
        write(self.out / "summary.json", self.result)

    def memory(self, label):
        workers, replicas = self.cluster.inventory(compact=True)
        write(self.out / f"inventory_{label}.json", {"workers": workers, "replicas": replicas})
        self.result["memory_samples"].append({"label": label, "path": str(self.out / f"inventory_{label}.json"),
            "workers": [{k: w[k] for k in ("worker", "node", "gpu", "epoch", "used_bytes", "staging_bytes",
                "hbm_allocated_bytes", "hbm_reserved_bytes", "headroom_bytes")} for w in workers]})
        self.save()
        return workers, replicas

    def command(self, slot, value):
        path = Path(slot["command"])
        self.modified_commands.setdefault(str(path), path.read_bytes())
        write(path, value)

    def parked_evidence(self, slot):
        if slot.get("parked") is not True:
            raise ValueError("explicit slot is not parked")
        command = json.loads(Path(slot["command"]).read_text())
        evidence = []
        for rank in range(2):
            path = Path(command["metrics"]) / f"rank{rank}.jsonl"
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            state_rows = [r for r in rows if r["event"] in ("load_begin", "weights_ready", "release_begin", "release_end", "load_failed")]
            if not state_rows or state_rows[-1]["event"] != "release_end":
                raise ValueError("latest rank lifecycle evidence does not prove parked state")
            evidence.append({"rank": rank, "path": str(path), "sha256": digest(path), "last_event": state_rows[-1]})
        return {"get_model_info": http(slot["endpoint"], "get_model_info"), "release_evidence": evidence}

    def preflight(self):
        self.before, replicas = self.memory("before")
        self.protected_before = source_signature(replicas)
        if any(w["transactions"] for w in self.before):
            raise ValueError("retained mesh has active transactions")
        self.base = select_base(self.model, replicas)
        write(self.out / "base_sources_before.json", self.base)
        self.result["base_source_signature"] = source_signature(self.base)
        slots = [self.target_slot] + ([self.source_slot] if self.source_slot else [])
        if self.source_slot and (self.source_slot["endpoint"] == self.target_slot["endpoint"]
                                 or set(self.source_slot["workers"]) & set(self.target_slot["workers"])):
            raise ValueError("S3 requires distinct source and target engines/devices")
        for slot in slots:
            if (slot.get("model", "B") != "B" or len(slot["workers"]) != 2 or len(set(slot["workers"])) != 2
                    or len({self.before[w]["node"] for w in slot["workers"]}) != 1):
                raise ValueError("each Qwen TP2 engine must be on one host")
            self.result.setdefault("parked_preflight", {})[slot["endpoint"]] = self.parked_evidence(slot)
            for source, wid in zip(self.model["sources"], slot["workers"]):
                if self.before[wid]["headroom_bytes"]["hbm"] < source["n"] + (self.args.guard_mib << 20):
                    raise MemoryError("insufficient physical HBM admission for complete rank weights")
                if slot is self.source_slot and self.before[wid]["budget_bytes"]["hbm"] - self.before[wid]["used_bytes"]["hbm"] < source["n"]:
                    raise MemoryError("insufficient source owner catalog budget")
        self.save()

    def case(self, workers, replicas, targets):
        base = select_base(self.model, replicas)
        owned_ids = set(self.owned)
        rows = [dict(r, source_role="base", allocation_id=r["id"]) for r in base]
        rows += [dict(r, source_role="active", allocation_id=r["id"]) for r in replicas if r["id"] in owned_ids]
        return {"scenario_id": self.args.scene, "workers": workers, "replicas": rows,
                "targets": targets, "profile": self.cluster.profile, "executor": "serial",
                "format_contract": self.result["format_contract"], "active_instances": self.active_instances,
                "budget": {"base_dram_bytes": sum(r.get("allocated_bytes", (r["n"]+4095)//4096*4096) for r in base),
                    "extra_hbm_bytes": 0, "location_extra_hbm_bytes": {},
                    "target_allocation_bytes": sum((s["n"]+4095)//4096*4096 for s in self.model["sources"]),
                    "staging_bytes": sum(w["staging_bytes"] for w in workers)}}

    def establish_active(self):
        started = time.monotonic()
        slot = self.source_slot
        targets = [target(s, f"{self.prefix}/active/rank{r}", slot["workers"][r]) for r, s in enumerate(self.model["sources"])]
        self.owned = [t["id"] for t in targets]
        self.result["owned_replica_ids"] = self.owned
        self.owner_started = time.monotonic()
        self.save()
        flow = self.cluster.transfer(self.cluster.plan_restore(targets, "resource_lp", self.model["source_ids"]))
        self.result["active_owner_build"] = flow
        if not flow["verification"]["passed"] or flow["verification"]["checked_bytes"] != sum(s["n"] for s in self.model["sources"]):
            raise AssertionError("complete source owner SHA verification failed")
        phase = self.prefix + "_active_attach"
        metrics, directory = self.out / "metrics" / phase, self.out / "ipc" / phase
        self.command(slot, {"tag": phase, "mode": "ipc", "metrics": str(metrics), "ipc_directory": str(directory)})
        with ThreadPoolExecutor(2) as pool:
            exports = list(pool.map(lambda t: http(self.state["endpoints"][t["worker"]], "export_ipc",
                {"id": t["id"], "directory": str(directory)}, timeout=300), targets))
        self.unreleased_slots[slot["endpoint"]] = slot
        http(slot["endpoint"], "resume_memory_occupation", {"tags": ["weights"]}, timeout=1200)
        slot["parked"] = False
        reply = generate(self.state, slot)
        if reply["text"] != self.model["reference"]["text"]:
            raise AssertionError("active source inference differs from saved reference")
        self.active_started = time.monotonic()
        evidence_path = self.out / "active_survival.json"
        survival = {"path": str(evidence_path), "alive_before": True, "protected_during_copy": True,
                    "external_existing": False, "additional_lifetime_s": 0.0, "additional_hbm_byte_seconds": 0.0,
                    "interference_evidence_path": str(evidence_path)}
        self.active_instances = [{"logical_id": self.prefix + "/active_instance", "replica_ids": self.owned,
                                  "epdt": 1.0, "survival_evidence": survival}]
        self.result["active_setup"] = {"wall_s": time.monotonic()-started, "exports": exports,
            "timing": summarize(metrics, 2), "before_reply": reply,
            "allocation_alias": "source engine imports the same keeper-owned HBM storages; no second weight allocation",
            "epdt_scope": "one protected source candidate; EPDT constant does not test candidate ranking"}
        write(evidence_path, {"source": "runtime", "before": {"correct": True, "reply": reply,
            "get_model_info": http(slot["endpoint"], "get_model_info")}, "protected_during_copy": True})
        self.memory("active_ready")

    def start_resume(self, slot):
        # A daemon RPC thread cannot make coordinator failure wait for the server's
        # long handoff timeout. Its pending consumer state prevents unsafe cleanup.
        future = Future()
        def call():
            try:
                future.set_result(http(slot["endpoint"], "resume_memory_occupation", {"tags": ["weights"]}, timeout=120))
            except BaseException as exc:
                future.set_exception(exc)
        self.resume_futures[slot["endpoint"]] = future
        threading.Thread(target=call, daemon=True, name="basic-resume-rpc").start()
        return future

    def restore(self, method, repetition):
        slot = self.target_slot
        phase = f"{self.prefix}_{method}_{repetition}"
        metrics = self.out / "metrics" / phase
        if metrics.exists() or (Path(self.state["handoff"]) / phase).exists():
            raise FileExistsError("handoff/metrics phase already exists")
        row = {"scene_id": self.args.scene, "method_id": method, "adaptation_identity": "mechanism",
               "repetition": repetition, "phase": phase, "status": "running"}
        self.result["cases"].append(row)
        targets = [target(s, f"{phase}/rank{r}", slot["workers"][r], mode="live", tag=phase,
                          metrics_directory=str(metrics)) for r, s in enumerate(self.model["sources"])]
        # Validate source eligibility and a fresh admission plan before the engine
        # can block on its handoff. Replan inside the measured request window.
        workers, replicas = self.cluster.inventory(compact=True)
        admission_case = self.case(workers, replicas, targets)
        admission = assess_case(admission_case, method)
        row["pre_resume_admission"] = admission
        if admission["status"] != "applicable" or admission["driver"] != "cluster":
            raise ValueError(f"method not executable before resume: {admission}")
        selected = [r for r in replicas if r["id"] in set(admission["source_ids"])]
        admission_plan = self.cluster.solve_restore(selected, targets, workers, admission["strategy"])
        if admission_plan["strategy"] != admission["strategy"]:
            raise RuntimeError("admission routing strategy fell back")
        if method == "janus_d2d" and admission.get("source_mode") != "active_d2d":
            raise AssertionError("S3 acceptance requires actual active D2D, not host fallback")
        self.command(slot, {"tag": phase, "mode": "restore", "metrics": str(metrics)})
        stamp = time.monotonic()
        self.unreleased_slots[slot["endpoint"]] = slot
        slot["parked"] = None
        self.save()
        future = self.start_resume(slot)
        wait = wait_for_exports(Path(self.state["handoff"]) / phase, ranks=2, timeout=120)
        workers, replicas = self.cluster.inventory(compact=True)
        case = self.case(workers, replicas, targets)
        assessment = assess_case(case, method)
        row["assessment"] = assessment
        write(self.out / f"{phase}_case.json", case)
        if assessment["status"] != "applicable" or assessment["driver"] != "cluster":
            raise ValueError(f"method no longer executable: {assessment}")
        selected = [r for r in replicas if r["id"] in set(assessment["source_ids"])]
        plan_started = time.monotonic()
        plan = self.cluster.solve_restore(selected, targets, workers, assessment["strategy"])
        row["online_planning_s"] = time.monotonic() - plan_started
        if plan["strategy"] != assessment["strategy"]:
            raise RuntimeError("routing strategy fell back")
        flow = self.cluster.transfer(plan, verify_before_ready=False, retain=True)
        future.result(timeout=120)
        self.resume_futures.pop(slot["endpoint"])
        slot["parked"] = False
        row.update(request_to_ready_ack_s=time.monotonic() - stamp, handoff_wait=wait, flow=flow,
                   timing=summarize(metrics, 2), source_availability=assessment["source_ids"])
        row["primary_T_ready_s"] = row["timing"]["load_s"]
        reply = generate(self.state, slot)
        row.update(reply=reply, correct=reply["text"] == self.model["reference"]["text"],
                   request_to_first_correct_token_s=reply["first_token_time"]-stamp if reply["text"] == self.model["reference"]["text"] else None)
        self.memory(f"{method}_{repetition}_target_ready")
        verify_started = time.monotonic()
        row["verification"] = self.cluster.verify(flow["tx"])
        row["post_ready_verification_s"] = time.monotonic()-verify_started
        if (not row["correct"] or row["verification"].get("passed") is not True
                or row["verification"]["checked_bytes"] != sum(s["n"] for s in self.model["sources"])):
            raise AssertionError("full-byte SHA or exact reference output failed")
        self.cluster.finish(flow["tx"])
        http(slot["endpoint"], "release_memory_occupation", {"tags": ["weights"]}, timeout=300)
        self.unreleased_slots.pop(slot["endpoint"])
        slot["parked"] = True
        self.command_keep.add(str(Path(slot["command"])))
        row["release_evidence"] = self.parked_evidence(slot)
        if self.source_slot:
            source_reply = generate(self.state, self.source_slot)
            row["active_after_reply"] = source_reply
            if source_reply["text"] != self.model["reference"]["text"]:
                raise AssertionError("active source did not survive copy correctly")
            survival = self.active_instances[0]["survival_evidence"]
            survival.update(alive_after=True, additional_lifetime_s=time.monotonic()-self.active_started,
                            additional_hbm_byte_seconds=sum(s["n"] for s in self.model["sources"])*(time.monotonic()-self.owner_started))
            write(Path(survival["path"]), {"source": "runtime", "survival_evidence": survival,
                "before": self.result["active_setup"]["before_reply"], "after": source_reply,
                "interference_scope": "two deterministic sequential correctness requests; sustained load not measured"})
            row["post_copy_assessment"] = assess_case(self.case(workers, replicas, targets), method)
        row.update(status="passed", measurement_status="measured", source_sha_checked_bytes=row["verification"]["checked_bytes"])
        self.memory(f"{method}_{repetition}_released")
        self.save()
        print(json.dumps({"scene_id": self.args.scene, "method_id": method, "T_ready_s": row["primary_T_ready_s"], "correct": row["correct"]}), flush=True)

    def cleanup(self):
        # Finish every verification mapping before releasing any engine allocation.
        for tx in list(self.cluster.active):
            try:
                self.cluster.finish(tx)
            except Exception as exc:
                self.result["cleanup_errors"].append("finish failed: " + repr(exc))
        pending = [endpoint for endpoint, future in self.resume_futures.items() if not future.done()]
        self.result["pending_resume_endpoints"] = pending
        if pending:
            self.result["cleanup_errors"].append("resume RPC pending; retain consumers/owners for owned-engine diagnosis")
        if not self.cluster.active and not pending:
            for endpoint, slot in list(self.unreleased_slots.items()):
                try:
                    http(endpoint, "release_memory_occupation", {"tags": ["weights"]}, timeout=300)
                    slot["parked"] = True
                    self.unreleased_slots.pop(endpoint)
                    self.command_keep.add(str(Path(slot["command"])))
                except Exception as exc:
                    self.result["cleanup_errors"].append("consumer release uncertain; retain owner: " + repr(exc))
        if not self.unreleased_slots and not self.cluster.active:
            for rid in list(reversed(self.owned)):
                try:
                    _, replicas = self.cluster.inventory(compact=True)
                    if rid in {r["id"] for r in replicas}:
                        self.cluster.retire(rid)
                    self.owned.remove(rid)
                except Exception as exc:
                    self.result["cleanup_errors"].append("owner retirement failed: " + repr(exc))
        self.result["remaining_owned_ids"] = self.owned
        self.result["unreleased_endpoints"] = list(self.unreleased_slots)
        try:
            after, replicas = self.memory("after")
            if hasattr(self, "base"):
                unchanged = source_signature(select_base(self.model, replicas)) == source_signature(self.base)
                self.result["retained_base_unchanged"] = unchanged
                if not unchanged:
                    raise AssertionError("retained base changed")
            if hasattr(self, "protected_before"):
                protected_after = source_signature([r for r in replicas if r["id"] not in set(self.result.get("owned_replica_ids", []))])
                self.result["protected_catalog_unchanged"] = protected_after == self.protected_before
                if not self.result["protected_catalog_unchanged"]:
                    raise AssertionError("unrelated retained catalog changed")
            if hasattr(self, "before") and not self.owned:
                target_workers = set(self.target_slot["workers"] + (self.source_slot["workers"] if self.source_slot else []))
                ledger = []
                for wid in sorted(target_workers):
                    old, new = self.before[wid], after[wid]
                    row = {"worker": wid, "catalog_hbm_before": old["used_bytes"]["hbm"], "catalog_hbm_after": new["used_bytes"]["hbm"],
                           "allocated_delta": new["hbm_allocated_bytes"]-old["hbm_allocated_bytes"],
                           "reserved_delta": new["hbm_reserved_bytes"]-old["hbm_reserved_bytes"],
                           "physical_free_delta": new["headroom_bytes"]["hbm"]-old["headroom_bytes"]["hbm"]}
                    ledger.append(row)
                    if row["catalog_hbm_after"] != row["catalog_hbm_before"] or max(row["allocated_delta"], row["reserved_delta"]) > self.args.allocation_tolerance_mib << 20:
                        raise AssertionError("owner allocator/catalog did not return to baseline")
                    if row["physical_free_delta"] < -(self.args.physical_tolerance_mib << 20):
                        raise AssertionError("physical HBM free did not recover within registered tolerance")
                self.result["cleanup_physical_ledger"] = ledger
        except Exception as exc:
            self.result["cleanup_errors"].append(repr(exc))
        # Keep successful release evidence; restore commands changed before resume.
        uncertain = {str(Path(slot["command"])) for slot in self.unreleased_slots.values() if "command" in slot}
        for path, old in self.modified_commands.items():
            if path not in self.command_keep and path not in uncertain:
                Path(path).write_bytes(old)
        self.result["source_hash_mismatches"] = [p for p, h in self.freeze.items() if digest(ROOT / p) != h]
        if self.result["source_hash_mismatches"]:
            self.result["cleanup_errors"].append("recorded code changed during run")
        self.save()

    def run(self):
        self.result["status"] = "running"
        try:
            self.preflight()
            if self.source_slot:
                self.establish_active()
            for method in self.args.methods:
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
    parser.add_argument("--session", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scene", choices=("S2", "S3"))
    parser.add_argument("--target-slot")
    parser.add_argument("--source-slot", default="target_a")
    parser.add_argument("--methods", nargs="+", choices=("janus_host", "janus_d2d", "fastest", "resource_lp"))
    parser.add_argument("--repetitions", type=int, choices=(1, 2), default=1)
    parser.add_argument("--guard-mib", type=int, default=2048)
    parser.add_argument("--allocation-tolerance-mib", type=int, default=32)
    parser.add_argument("--physical-tolerance-mib", type=int, default=256)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--cleanup-bootstrap", type=Path)
    parser.add_argument("--reference-session", type=Path, default=ROOT / "codex_coding/results/motivation/fig4_lanes11/session.json")
    parser.add_argument("--model-path", type=Path, default=Path("/data/node28/shared/zhujianian/Qwen3.6-35B-A3B"))
    parser.add_argument("--port-base", type=int, default=33000)
    parser.add_argument("--source-port", type=int, default=33240)
    parser.add_argument("--target-port", type=int, default=33340)
    parser.add_argument("--nccl-port", type=int, default=33440)
    parser.add_argument("--source-memory-gib", type=int, default=160)
    parser.add_argument("--target-memory-gib", type=int, default=64)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    if args.bootstrap or args.cleanup_bootstrap:
        if args.scene or args.session or (args.bootstrap and args.cleanup_bootstrap):
            parser.error("bootstrap/cleanup are separate from scenario execution")
        ports = [args.port_base, args.port_base+1, args.port_base+100, args.source_port, args.target_port, args.nccl_port, args.nccl_port+1]
        if len(set(ports)) != len(ports) or any(p < 1024 or p > 65535 for p in ports):
            parser.error("bootstrap ports must be distinct and in 1024..65535")
        if args.source_memory_gib < 128 or args.target_memory_gib < 32:
            parser.error("bootstrap memory caps below registered minimum")
        if args.cleanup_bootstrap and not args.execute:
            parser.error("cleanup-bootstrap requires explicit --execute")
        result = cleanup_bootstrap(args.cleanup_bootstrap, args.out) if args.cleanup_bootstrap else bootstrap(args)
        print(json.dumps({k: result[k] for k in ("status", "overlay_session", "error") if k in result}))
        if result["status"] == "failed":
            raise SystemExit(1)
        return
    if not args.session or not args.scene:
        parser.error("scenario execution requires --session and --scene")
    args.target_slot = args.target_slot or ("target_a" if args.scene == "S2" else "target_b")
    args.methods = args.methods or (["janus_host", "resource_lp"] if args.scene == "S2" else ["janus_d2d", "resource_lp"])
    if args.scene == "S2" and "janus_d2d" in args.methods:
        parser.error("S2 has no active source; use janus_host explicitly")
    if len(args.methods) != len(set(args.methods)):
        parser.error("duplicate methods would reuse metric phase names")
    runner = ScenarioRun(args)
    if args.execute and not runner.run():
        raise SystemExit(1)
    if not args.execute:
        print(json.dumps({"status": "registered_only", "measurement_status": "not_measured", "out": str(runner.out)}))


if __name__ == "__main__":
    main()
