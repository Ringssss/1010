#!/usr/bin/env python3
"""Bounded real-weight keeper bridge; execute only on the target node29 host."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import resource
import socket
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "main_lib"))
sys.path.insert(0, str(ROOT / "codex_coding/src/v0.1.23.3/task2/basic_scenarios"))
sys.path.insert(0, str(ROOT / "codex_coding/src/v0.1.23.3/task2/topology_scenarios/calibration"))
import inventory as retained
from runtime_support import select_base
from fluidckpt.cluster.control import Cluster, request
from fluidckpt.benchmarks.topology_calibration import signature
from fluidckpt.benchmarks.keeper_bridge import (SCHEMA, CASES, targets_for, lease_check,
    protected_workers, rpc_window)

RESULT_ROOT = ROOT / "codex_coding/results/v0.1.23.3/task2/keeper_bridge"
SCRIPT = Path(__file__).resolve()
PHYSICAL_TOLERANCE_BYTES = 128 << 20


def code_freeze():
    paths = [SCRIPT, ROOT / "main_lib/fluidckpt/benchmarks/keeper_bridge.py",
        ROOT / "codex_coding/src/v0.1.23.3/task2/basic_scenarios/runtime_support.py",
        ROOT / "codex_coding/src/v0.1.23.3/task2/topology_scenarios/calibration/inventory.py"]
    paths += [ROOT / "main_lib/fluidckpt" / p for p in (
        "cluster/control.py", "cluster/worker.py", "cluster/wire.py", "cluster/planning.py",
        "cluster/path_catalog.py", "cluster/catalog.py", "cluster/managed_control.py",
        "serving/keeper.py", "serving/replicas.py", "executor/chunks.py",
        "executor/pipeline.py", "benchmarks/topology_calibration.py")]
    return {str(p.relative_to(ROOT)): retained.digest(p) for p in paths}


def write(path, value):
    retained.write(path, value)


def runtime_facts(state, node):
    evidence = []
    for row in retained.protected_specs(state, node):
        if not row["passed"]:
            raise RuntimeError("protected PID no longer belongs to its registered process")
        spec = json.loads(Path(row["spec"]).read_text())
        if "fluidckpt.cluster.worker" not in spec["command"]:
            continue
        pid = row["registered"]["pid"]
        raw = Path(f"/proc/{pid}/environ").read_bytes()
        env = dict(part.split("=", 1) for part in raw.decode(errors="replace").split("\0") if "=" in part)
        chosen = {k: v for k, v in env.items() if k.startswith(("NCCL_", "RCCL_", "GLOO_",
                   "FLUIDCKPT_EXECUTE_PROFILE", "HIP_VISIBLE", "HSA_", "AMD_SERIALIZE"))}
        paths = [Path(spec["log"])]
        if env.get("NCCL_DEBUG_FILE"):
            paths.append(Path(env["NCCL_DEBUG_FILE"].replace("%p", str(pid)).replace("%h", socket.gethostname())))
        logs = []
        for path in dict.fromkeys(paths):
            if path.is_file():
                data = path.read_bytes()
                logs.append({"path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
                    "text": data.decode(errors="replace")})
            else:
                logs.append({"path": str(path), "exists": False})
        evidence.append(dict(row, actual_environment=chosen, environ_sha256=hashlib.sha256(raw).hexdigest(),
            logs=logs, gpu=spec["command"][spec["command"].index("--gpu")+1]))
    return {"node": node, "clock_domain": retained.clock_domain(), "keepers": evidence, "read_only": True}


class ObservedCluster(Cluster):
    """Observe existing HTTP gates while keeping Cluster.transfer and serial executor."""
    def __init__(self, state, directory, timeout):
        super().__init__(state["endpoints"], state["profile"], executor="serial")
        self.directory, self.timeout = directory, timeout
        self.observations, self.phase_snapshots = [], {}
        self.domain = retained.clock_domain()
        self.counter_evidence = {}

    def counters(self):
        return {p.name: retained.counters(p.name, "ib0")
                for p in Path("/sys/class/infiniband").glob("*")}

    def call_all(self, operation, bodies=None, telemetry=None):
        if operation == "execute":
            self.counter_evidence["idle_before"] = self.counters()
            time.sleep(.2)
            self.counter_evidence["idle_after"] = self.counters()
            self.counter_evidence["execute_before"] = self.counters()
        def one(worker):
            rpc = {}
            row = {"operation": operation, "worker": worker, "clock_domain": self.domain,
                   "begin_ns": time.monotonic_ns()}
            try:
                response = request(self.endpoints[worker], operation,
                    None if bodies is None else bodies[worker], timeout=self.timeout, telemetry=rpc)
                row["passed"] = True
                return response, rpc, row
            except Exception as exc:
                row.update(passed=False, error=repr(exc))
                raise
            finally:
                row["end_ns"] = time.monotonic_ns()
                self.observations.append(row)
        with ThreadPoolExecutor(len(self.endpoints)) as pool:
            futures = [pool.submit(one, w) for w in range(len(self.endpoints))]
            rows, errors = [], []
            for future in futures:
                try:
                    response, rpc, _ = future.result()
                    rows.append(response)
                    if telemetry is not None:
                        telemetry.append(rpc)
                except Exception as exc:
                    errors.append(repr(exc)); rows.append(None)
        if errors:
            raise RuntimeError(f"{operation}: {errors}")
        if operation == "execute":
            self.counter_evidence["execute_after"] = self.counters()
            write(self.directory / "receiver_counters.json", self.counter_evidence)
        if operation in ("prepare", "execute", "complete"):
            workers, _ = self.inventory(compact=True)
            self.phase_snapshots[operation] = workers
            write(self.directory / f"after_{operation}.json", workers)
        write(self.directory / "rpc_events.json", self.observations)
        return rows

    def verify(self, tx):
        begin = time.monotonic_ns()
        result = super().verify(tx)
        self.observations.append({"operation": "verify_gate", "begin_ns": begin,
            "end_ns": time.monotonic_ns(), "clock_domain": self.domain, "worker": None, "passed": True})
        return result


def cleanup_owned(cluster, run_id, owned):
    workers, _ = cluster.inventory(compact=True)
    entries = {r["id"]: r for w in workers for r in w["replicas"]}
    result = []
    for rid in owned:
        if not rid.startswith(run_id+"/"):
            raise RuntimeError("cleanup outside exact run namespace")
        row = entries.get(rid)
        if row and row["state"] == "VALID":
            result.append({"id": rid, "reply": cluster.retire(rid)})
        elif row and row["state"] != "RELEASED":
            raise RuntimeError(f"unresolved owned target state: {rid}: {row['state']}")
    return result


def run_case(state, model, out, run_id, case, nbytes, off, timeout):
    directory = out / case
    directory.mkdir()
    cluster = ObservedCluster(state, directory, timeout)
    canonical_case = "pair" if case.startswith("warmup") else case
    targets = targets_for(model, run_id, canonical_case, nbytes, off)
    # Warmup ownership is distinct from the measured pair.
    if case.startswith("warmup"):
        for target in targets:
            target["key"] = target["id"] = target["id"].replace("/pair/", f"/{case}/")
    owned = [t["id"] for t in targets]
    result = {"case": case, "payload_bytes_per_flow": nbytes, "off": off,
        "sample_role": "unmeasured_communicator_prewarm" if case.startswith("warmup") else "single_observation",
        "status": "running", "owned_target_ids": owned, "executor": "serial",
        "coordinator_duration_scope": "instrumented Cluster gate durations include after-phase inventory snapshots and 200ms idle-counter sample; use raw RPC envelopes for transfer timing"}
    write(directory / "summary.json", result)
    tx = None
    try:
        before, replicas = cluster.inventory(compact=True)
        base = select_base(model, replicas)
        plan = cluster.solve_restore(base, targets, before, "nearest")
        write(directory / "plan.json", plan)
        result["flow"] = flow = cluster.transfer(plan, verify_before_ready=True, retain=True)
        tx = flow["tx"]
        selected_ids = sorted({o["replica_id"] for o in plan["ops"]})
        result["source_lease_gates"] = {phase: lease_check(cluster.phase_snapshots[phase], selected_ids, True)
            for phase in ("prepare", "execute", "complete")}
        if not all(result["source_lease_gates"].values()):
            raise RuntimeError("source leases missing during transfer gates")
        calls = [r for r in cluster.observations if r["operation"] == "execute"]
        result["receiver_rpc_envelope"] = rpc_window(calls, [t["worker"] for t in targets], cluster.domain)
        result["worker_operation_elapsed_s"] = {str(w["worker"]): w["records"] for w in flow["execution"] if w["records"]}
        result["phase_rpc_envelopes"] = {phase: rpc_window(
            [r for r in cluster.observations if r["operation"] == phase], list(range(4)), cluster.domain)
            for phase in ("prepare", "execute", "verify_run", "complete")}
        cluster.finish(tx); tx = None
        result["retirement"] = cleanup_owned(cluster, run_id, owned)
        final, _ = cluster.inventory(compact=True)
        result["source_leases_released"] = lease_check(final, model["source_ids"], False)
        result["status"] = "passed" if result["source_leases_released"] else "failed"
    except BaseException as exc:
        result.update(status="failed", error=repr(exc), traceback=traceback.format_exc())
    finally:
        if tx:
            try:
                cluster.finish(tx)
            except Exception as exc:
                result.setdefault("cleanup_errors", []).append(repr(exc))
        try:
            result["final_retirement"] = cleanup_owned(cluster, run_id, owned)
        except Exception as exc:
            result.setdefault("cleanup_errors", []).append(repr(exc))
        if result.get("cleanup_errors"):
            result["status"] = "failed"
        write(directory / "rpc_events.json", cluster.observations)
        write(directory / "summary.json", result)
    return result


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--bytes", type=int, default=1 << 30)
    parser.add_argument("--off", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--protect-session", type=Path, action="append", default=[])
    parser.add_argument("--node-runtime", choices=("node27", "node29"))
    parser.add_argument("--transfer-only", action="store_true")
    args = parser.parse_args()
    state = retained.load_session(args.session.resolve())
    if args.node_runtime:
        print(json.dumps(runtime_facts(state, args.node_runtime)))
        return
    if not args.out or RESULT_ROOT not in args.out.resolve().parents or not args.out.name.startswith("bridge_"):
        parser.error("use an unused keeper_bridge/bridge_* result directory")
    if not 0 < args.bytes <= 1 << 30 or not 0 < args.timeout <= 300:
        parser.error("bridge bound is at most 1GiB/flow, HTTP timeout at most 300s")
    out = args.out.resolve()
    model = state["models"]["B"]
    if args.transfer_only:
        if not args.execute or not out.is_dir() or (out / "transfer_summary.json").exists():
            raise RuntimeError("target child requires a registered parent directory and unique transfer summary")
        preflight = json.loads((out / "inventory_before.json").read_text())
        if preflight["nodes"]["node29"]["clock_domain"] != retained.clock_domain():
            raise RuntimeError("transfer child must execute on the registered node29 clock domain")
        child = {"status": "running", "cases": [], "coordinator_domain": retained.clock_domain(),
            "scope": "target-node transfer child only; all SSH/protection collection remains in node27 controller"}
        write(out / "transfer_summary.json", child)
        try:
            for case, nbytes in [("warmup_pairs", 32 << 20), *[(c, args.bytes) for c in CASES]]:
                row = run_case(state, model, out, out.name, case, nbytes, args.off, args.timeout)
                child["cases"].append(row); write(out / "transfer_summary.json", child)
                if row["status"] != "passed":
                    raise RuntimeError(f"case failed: {case}; stop bounded sequence")
            child["status"] = "passed"
        except BaseException as exc:
            child.update(status="failed", error=repr(exc), traceback=traceback.format_exc())
        write(out / "transfer_summary.json", child)
        print(json.dumps({"status": child["status"], "out": str(out)}))
        if child["status"] != "passed":
            raise SystemExit(1)
        return
    out.mkdir(parents=True, exist_ok=False); os.chmod(out, 0o777)
    result = {"schema_version": SCHEMA, "status": "registered", "session": str(args.session.resolve()),
        "session_sha256": retained.digest(args.session), "run_id": out.name,
        "scope": "real retained DRAM to bounded HBM replica via existing Cluster.transfer serial; no model loader/forward",
        "T_ready": None, "worker_true_monotonic_window": None,
        "cases": [], "coordinator_domain": retained.clock_domain(),
        "physical_hbm_free_tolerance_bytes": PHYSICAL_TOLERANCE_BYTES,
        "code_sha256": code_freeze(), "input_sha256": {str(p.resolve()): retained.digest(p)
            for p in [args.session, *args.protect_session]}}
    for case in CASES:
        targets_for(model, out.name, case, args.bytes, args.off)
    write(out / "summary.json", result)
    if not args.execute:
        print(json.dumps({"status": "registered", "out": str(out)})); return
    before = None
    try:
        before = retained.inspect(state, args.session.resolve(), "shca_0", "ib0", protect_sessions=args.protect_session)
        write(out / "inventory_before.json", before)
        if not before["resource_admission"]["passed"]:
            raise RuntimeError(before["resource_admission"])
        if before["nodes"]["node27"]["clock_domain"] != retained.clock_domain():
            raise RuntimeError("parent controller must execute on node27; retained remote helper is node27-local")
        for worker in before["workers"][2:]:
            if worker["headroom_bytes"]["hbm"] < args.bytes+(2 << 30):
                raise RuntimeError("target HBM headroom is below payload plus 2GiB guard")
        for node in state["nodes"]:
            facts = json.loads(retained.remote(state, node, ["python3", str(SCRIPT), "--session",
                str(args.session.resolve()), "--node-runtime", node]))
            write(out / f"{node}_runtime_before.json", facts)
        result["status"] = "running"; write(out / "summary.json", result)
        reply = retained.remote(state, "node29", ["python3", str(SCRIPT), "--session", str(args.session.resolve()),
            "--out", str(out), "--bytes", str(args.bytes), "--off", str(args.off), "--timeout", str(args.timeout),
            "--execute", "--transfer-only"], timeout=args.timeout*8+60)
        result["target_child_reply"] = reply
        child = json.loads((out / "transfer_summary.json").read_text())
        result["cases"] = child["cases"]
        result["target_coordinator_domain"] = child["coordinator_domain"]
        if child["status"] != "passed":
            raise RuntimeError("target child failed; see transfer_summary.json")
        result["status"] = "passed"
    except BaseException as exc:
        result.update(status="failed", error=repr(exc), traceback=traceback.format_exc())
    finally:
        if (out / "transfer_summary.json").is_file():
            child = json.loads((out / "transfer_summary.json").read_text())
            result["cases"] = child.get("cases", [])
            result["target_coordinator_domain"] = child.get("coordinator_domain")
            result["target_child_status"] = child.get("status")
        if before:
            try:
                after = retained.inspect(state, args.session.resolve(), "shca_0", "ib0", protect_sessions=args.protect_session)
                write(out / "inventory_after.json", after)
                adjusted = dict(after, workers=protected_workers(after["workers"], out.name))
                adjusted["protected_catalog_signature"] = signature(adjusted["workers"])
                result["protection"] = retained.preservation(before, adjusted)
                result["physical_hbm_free_delta_bytes"] = {str(w["worker"]):
                    after["workers"][w["worker"]]["headroom_bytes"]["hbm"]-w["headroom_bytes"]["hbm"] for w in before["workers"]}
                if not result["protection"]["passed"] or any(v < -PHYSICAL_TOLERANCE_BYTES for v in result["physical_hbm_free_delta_bytes"].values()):
                    raise RuntimeError("catalog/process/parked/legacy/physical HBM protection failed")
                for node in state["nodes"]:
                    facts = json.loads(retained.remote(state, node, ["python3", str(SCRIPT), "--session",
                        str(args.session.resolve()), "--node-runtime", node]))
                    write(out / f"{node}_runtime_after.json", facts)
            except BaseException as exc:
                result.update(status="failed", protection_error=repr(exc))
        result["code_sha256_after"] = code_freeze()
        result["input_sha256_after"] = {str(p.resolve()): retained.digest(p) for p in [args.session, *args.protect_session]}
        result["code_and_inputs_unchanged"] = (result["code_sha256"] == result["code_sha256_after"]
            and result["input_sha256"] == result["input_sha256_after"])
        if not result["code_and_inputs_unchanged"]:
            result.update(status="failed", hash_error="frozen direct dependencies or protected input changed")
        write(out / "summary.json", result)
    print(json.dumps({"status": result["status"], "out": str(out)}))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
