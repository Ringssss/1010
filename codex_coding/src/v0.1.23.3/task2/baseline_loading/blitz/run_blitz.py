"""Run the single-target BlitzScale TP distribution adaptation on a retained session."""
import argparse
import copy
import json
import os
from pathlib import Path
import resource
import sys
import time

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "codex_coding/src/v0.1.23.3/task2/basic_scenarios"))
sys.path.insert(0, str(ROOT / "main_lib"))
from run_scenarios import ScenarioRun
from runtime_support import digest, format_contract, generate, http, load_state, target, write
from blitz_freeze import code_freeze
from fluidckpt.benchmarks.blitz_adapter import BlitzScaleAdapter, IDENTITY, ORIGIN, UPSTREAM_SHA, bind_blitz_plan
from fluidckpt.cluster.control import Cluster
from fluidckpt.serving.handoff import wait_for_exports
from fluidckpt.serving.measure import summarize

RESULT_ROOT = ROOT / "codex_coding/results/v0.1.23.3/task2/baseline_loading"


class BlitzRun(ScenarioRun):
    def __init__(self, args):
        # Keep ScenarioRun's state contract; customize registration without writing
        # outside this adapter's namespace. All runtime lifecycle methods are reused.
        self.args, self.out = args, args.out
        self.out.mkdir(parents=True, exist_ok=False)
        os.chmod(self.out, 0o777)
        self.state = load_state(args.session)
        self.model = self.state["models"]["B"]
        if self.model["tp"] != 2:
            raise ValueError("Blitz S3 gate requires the complete processed Qwen TP2 snapshot")
        self.target_slot = copy.deepcopy(self.state["slots"][args.target_slot])
        self.source_slot = copy.deepcopy(self.state["slots"][args.source_slot])
        self.cluster = Cluster(self.state["endpoints"], self.state["profile"], executor="serial")
        self.prefix = "blitz_"+digest(args.session)[:8]+"_"+self.out.name
        self.owned, self.active_instances = [], []
        self.modified_commands, self.unreleased_slots, self.resume_futures = {}, {}, {}
        self.command_keep = set()
        self.freeze = code_freeze()
        self.adapter = BlitzScaleAdapter(args.cache_ttl_ms)
        self.result = {"status": "registered", "measurement_status": "not_measured",
            "scene_id": "S3", "scenario_id": "S3", "run_id": self.out.name,
            "development": "v0.1.23.3", "task": "task2", "experiment_id": "e1", "gate_kind": "capability_gate",
            "cases": [], "cleanup_errors": [], "memory_samples": [],
            "methods": {"blitz_scale": self.adapter.method()}, "adaptation_identity": IDENTITY,
            "frozen_upstream_commits": {"BlitzScale": UPSTREAM_SHA}, "upstream_origin": ORIGIN,
            "session_sha256": digest(args.session), "format_contract": format_contract(self.model),
            "protocol": {"scene": "S3", "repetitions": args.repetitions,
                "source_selection": "frozen Steersman + span_max_rdma_sending_replicas + pop source",
                "source_availability": "retained DRAM plus one actual active IPC-backed TP2 instance",
                "primary_metric": "same-host max(weights_ready)-min(load_begin)",
                "request_metric": "coordinator request through all-rank resume acknowledgment",
                "verification": "full transfer SHA and exact 32-token output after readiness",
                "build_cost": "source HBM build and IPC attachment outside restore; recorded separately",
                "cleanup": "inherited ScenarioRun release-before-retirement and retained-base protections",
                "baseline_scope": "single target TP distribution; no original CUDA/RDMA backend or live serving",
                "failure_fallback": "record and abort failed transfers; CPU pool fallback not_supported; no method substitution",
                "foreground_scope": "source correctness before/after copy; sustained traffic not measured",
                "code_freeze_scope": "explicit runner/retained-worker/engine entries plus transitive FluidCKPT imports and pinned upstream policy files",
                "code_freeze_excludes": "unrelated baselines, test/audit scripts, inactive experiment launchers; no repository-wide glob",
                "physical_peak_verified": False}}
        self.result["code_sha256"] = self.freeze
        write(self.out / "commands.txt.json", {"argv": [sys.executable, *sys.argv]})
        self.save()

    def build(self, targets):
        workers, replicas = self.cluster.inventory(compact=True)
        case = self.case(workers, replicas, targets)
        plan = bind_blitz_plan(self.adapter.plan(case), workers, self.state["endpoints"])
        return case, plan

    def restore(self, method, repetition):
        slot = self.target_slot
        phase = f"{self.prefix}_{method}_{repetition}"
        metrics = self.out / "metrics" / phase
        if metrics.exists() or (Path(self.state["handoff"]) / phase).exists():
            raise FileExistsError("handoff/metrics phase already exists")
        row = {"scene_id": "S3", "method_id": method, "adaptation_identity": IDENTITY,
               "repetition": repetition, "phase": phase, "status": "running", "commit": UPSTREAM_SHA}
        self.result["cases"].append(row)
        targets = [target(s, f"{phase}/rank{r}", slot["workers"][r], mode="live", tag=phase,
                          metrics_directory=str(metrics)) for r, s in enumerate(self.model["sources"])]
        admission_case, admission_plan = self.build(targets)
        row["pre_resume_admission"] = admission_plan["blitz"]
        if admission_plan["blitz"]["source_mode"] != "rdma_TP_chain_single_target":
            raise AssertionError("this S3 gate requires a remote complete active TP source")
        write(self.out / f"{phase}_admission.json", admission_case)
        self.command(slot, {"tag": phase, "mode": "restore", "metrics": str(metrics)})
        stamp = time.monotonic()
        self.unreleased_slots[slot["endpoint"]] = slot
        slot["parked"] = None
        self.save()
        future = self.start_resume(slot)
        wait = wait_for_exports(Path(self.state["handoff"]) / phase, ranks=2, timeout=120)
        started = time.monotonic()
        case, plan = self.build(targets)
        row["online_inventory_and_planning_s"] = time.monotonic()-started
        row["online_planning_s"] = plan["planning_s"]
        write(self.out / f"{phase}_case.json", case)
        write(self.out / f"{phase}_plan.json", plan)
        flow = self.adapter.transfer(self.cluster, plan, verify_before_ready=False, retain=True)
        future.result(timeout=120)
        self.resume_futures.pop(slot["endpoint"])
        slot["parked"] = False
        row.update(request_to_ready_ack_s=time.monotonic()-stamp, handoff_wait=wait, flow=flow,
                   timing=summarize(metrics, 2), source_availability=plan["blitz"]["source_ids"],
                   resource_ledger=plan["resource_ledger"], route_origin=plan["blitz"])
        row["assessment"] = {"status": "applicable", "driver": "cluster", "identity": "hardware_adaptation",
                             "source_ids": plan["blitz"]["source_ids"], "strategy": plan["strategy"],
                             "resource_ledger": plan["resource_ledger"], "source_mode": plan["blitz"]["source_mode"]}
        row["primary_T_ready_s"] = row["timing"]["load_s"]
        reply = generate(self.state, slot)
        row.update(reply=reply, correct=reply["text"] == self.model["reference"]["text"])
        row["request_to_first_correct_token_s"] = reply["first_token_time"]-stamp if row["correct"] else None
        self.memory(f"{method}_{repetition}_target_ready")
        started = time.monotonic()
        row["verification"] = self.cluster.verify(flow["tx"])
        row["post_ready_verification_s"] = time.monotonic()-started
        if (not row["correct"] or row["verification"].get("passed") is not True
                or row["verification"]["checked_bytes"] != sum(s["n"] for s in self.model["sources"])):
            raise AssertionError("full-byte SHA or exact reference output failed")
        self.cluster.finish(flow["tx"])
        http(slot["endpoint"], "release_memory_occupation", {"tags": ["weights"]}, timeout=300)
        self.unreleased_slots.pop(slot["endpoint"])
        slot["parked"] = True
        self.command_keep.add(str(Path(slot["command"])))
        row["release_evidence"] = self.parked_evidence(slot)
        source_reply = generate(self.state, self.source_slot)
        row["active_after_reply"] = source_reply
        if source_reply["text"] != self.model["reference"]["text"]:
            raise AssertionError("active source did not survive copy correctly")
        survival = self.active_instances[0]["survival_evidence"]
        survival.update(alive_after=True, additional_lifetime_s=time.monotonic()-self.active_started,
                        additional_hbm_byte_seconds=sum(s["n"] for s in self.model["sources"])*(time.monotonic()-self.owner_started))
        row["source_lifetime"] = dict(survival)
        write(Path(survival["path"]), {"source": "runtime", "survival_evidence": survival,
            "before": self.result["active_setup"]["before_reply"], "after": source_reply,
            "interference_scope": "sequential correctness requests; sustained traffic not measured"})
        row.update(status="passed", measurement_status="measured", source_sha_checked_bytes=row["verification"]["checked_bytes"])
        self.memory(f"{method}_{repetition}_released")
        self.save()
        print(json.dumps({"method_id": method, "T_ready_s": row["primary_T_ready_s"], "correct": row["correct"]}), flush=True)


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-slot", default="target_a")
    parser.add_argument("--target-slot", default="target_b")
    parser.add_argument("--repetitions", type=int, choices=(1, 2), default=1)
    parser.add_argument("--cache-ttl-ms", type=int, default=60000)
    parser.add_argument("--guard-mib", type=int, default=2048)
    parser.add_argument("--allocation-tolerance-mib", type=int, default=32)
    parser.add_argument("--physical-tolerance-mib", type=int, default=256)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    args.out = args.out.resolve()
    if RESULT_ROOT.resolve() not in args.out.parents or not args.out.name.startswith("blitz_"):
        parser.error("out must be a fresh blitz_* directory below baseline_loading")
    args.scene, args.methods = "S3", ["blitz_scale"]
    runner = BlitzRun(args)
    if args.execute:
        if not runner.run():
            raise SystemExit(1)
    else:
        print(json.dumps({"status": "registered_only", "measurement_status": "not_measured", "out": str(runner.out)}))


if __name__ == "__main__":
    main()
