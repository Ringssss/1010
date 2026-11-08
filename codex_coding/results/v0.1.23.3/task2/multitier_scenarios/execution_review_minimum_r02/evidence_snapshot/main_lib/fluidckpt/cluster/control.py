"""Single-coordinator cluster control with explicit prepare/execute/verify/publication gates."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid

from .planning import check_retirement, placement_plan, restore_plan
from .wire import planning_payload, bind_manifests, worker_plan, planning_context, checked_epochs
from .managed_control import ManagedPlacementMixin
from fluidckpt.executor.chunks import execution_chunk

CONTROLLER_CLOCK_DOMAIN = f"controller/{socket.gethostname()}/{os.getpid()}/{uuid.uuid4().hex}"


def request(endpoint, operation, body=None, timeout=300, telemetry=None):
    started = time.monotonic()
    data = None if body is None else json.dumps(body).encode()
    serialized = time.monotonic()
    req = urllib.request.Request(endpoint+"/"+operation,
        data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = response.read()
            received = time.monotonic()
            result = json.loads(payload) if payload else {}
            if telemetry is not None:
                telemetry.update(operation=operation, request_bytes=len(data) if data else 0,
                    response_bytes=len(payload), serialize_s=serialized-started,
                    transport_and_server_s=received-serialized, deserialize_s=time.monotonic()-received)
            return result
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{endpoint}/{operation}: {exc.read().decode()}") from exc


class Cluster(ManagedPlacementMixin):
    def __init__(self, endpoints, profile, planner_endpoint=None, control_mode="compact", executor="serial"):
        if control_mode not in ("compact", "legacy"):
            raise ValueError("unsupported control mode")
        if executor not in ("serial", "double_buffer"):
            raise ValueError("unsupported executor")
        self.endpoints = endpoints
        self.profile = dict(profile)
        # Both executors complete one target operation before starting its next
        # source path. Overlap within a copy does not parallelize different paths.
        self.profile.setdefault('serial_target_execution', True)
        self.planner_endpoint = planner_endpoint
        self.control_mode = control_mode
        self.executor = executor
        self.lock = threading.RLock()
        self.active = {}
        self.placement_journals = {}

    def call_all(self, operation, bodies=None, telemetry=None):
        reports = [{} for _ in self.endpoints]
        with ThreadPoolExecutor(len(self.endpoints)) as pool:
            futures = [pool.submit(request, ep, operation, None if bodies is None else bodies[i],
                                   telemetry=reports[i] if telemetry is not None else None)
                       for i, ep in enumerate(self.endpoints)]
            rows, failures = [], []
            for future in futures:
                try: rows.append(future.result())
                except Exception as exc: failures.append(str(exc)); rows.append(None)
        if telemetry is not None: telemetry.extend(reports)
        if failures: raise RuntimeError(f"{operation} failed: {failures}")
        return rows

    def inventory(self, compact=False, telemetry=None):
        workers = self.call_all("inventory?compact=1" if compact else "inventory", telemetry=telemetry)
        if [w["worker"] for w in workers] != list(range(len(workers))):
            raise ValueError("worker identities do not match endpoint order")
        if len({w["session"] for w in workers}) != 1:
            raise ValueError("mixed cluster sessions")
        return workers, [r for w in workers for r in w["replicas"] if r["state"] == "VALID"]

    def plan_restore(self, targets, strategy="nearest", source_ids=None):
        started = time.monotonic()
        inventory_rpc = []
        workers, replicas = self.inventory(compact=self.control_mode == "compact", telemetry=inventory_rpc)
        inventoried = time.monotonic()
        if source_ids is not None:
            replicas = [r for r in replicas if r["id"] in source_ids]
        plan = self.solve_restore(replicas, targets, workers, strategy)
        plan["control_durations_s"] = {"inventory_rpc_s": inventoried-started,
            "planner_rpc_s": time.monotonic()-inventoried, "total_s": time.monotonic()-started}
        plan["inventory_rpc"] = inventory_rpc
        return plan

    def solve_restore(self, replicas, targets, workers, strategy="nearest"):
        compact = self.control_mode == "compact"
        telemetry = {}
        if self.planner_endpoint:
            body = planning_payload(replicas, targets, workers, self.profile, strategy) if compact else {
                "replicas": replicas, "targets": targets, "workers": workers,
                "profile": self.profile, "strategy": strategy}
            plan = request(self.planner_endpoint, "plan_restore", body, telemetry=telemetry)
            if compact: plan = bind_manifests(plan, targets)
        else:
            plan = restore_plan(replicas, targets, workers, self.profile, strategy)
        plan["control_context"] = planning_context(workers, self.endpoints)
        plan["control_mode"] = self.control_mode
        plan["planner_rpc"] = telemetry
        return plan

    def verify(self, tx):
        rows = self.call_all("verify_run", [{"tx": tx} for _ in self.endpoints])
        combined = {}
        for worker in rows:
            for row in worker["rows"]:
                target = combined.setdefault(row["op_id"], {})
                for k, value in row.items():
                    if k in target and target[k] != value:
                        raise RuntimeError("inconsistent transfer verification metadata")
                    target[k] = value
        plan = self.active[tx]
        if len(combined) != len(plan["ops"]): raise RuntimeError("missing verification operation")
        for op in plan["ops"]:
            r = combined[op["op_id"]]
            if (r.get("n") != op["n"] or not r.get("source_sha256")
                    or r["source_sha256"] != r.get("target_sha256")):
                raise RuntimeError(f"content verification failed for op {op['op_id']}")
        return {"passed": True, "checked_bytes": sum(o["n"] for o in plan["ops"]),
                "algorithm": "SHA256 of every logical byte per operation", "operations": combined}

    def transfer(self, plan, verify_before_ready=True, retain=False):
        with self.lock:
            plan = dict(plan, executor=plan.get("executor", self.executor))
            execution_chunk(plan)
            if plan["executor"] not in ("serial", "double_buffer"):
                raise ValueError("unsupported executor")
            started_ns = time.monotonic_ns()
            started = time.monotonic()
            inventory_rpc = []
            reused = self.control_mode == "compact" and "control_context" in plan
            if reused:
                # prepare() validates every worker epoch and acquires generation-checked source
                # leases. A changed worker or replica therefore fails before any collective.
                epochs = checked_epochs(plan["control_context"], self.endpoints)
            else:
                workers, _ = self.inventory(compact=self.control_mode == "compact", telemetry=inventory_rpc)
                epochs = {str(w["worker"]): w["epoch"] for w in workers}
            participants = list(range(len(self.endpoints)))
            inventoried = time.monotonic()
            tx = uuid.uuid4().hex
            bodies = [{"tx": tx, "epochs": epochs,
                       "plan": worker_plan(plan, w) if self.control_mode == "compact" else plan}
                      for w in participants]
            self.active[tx] = plan
            prepare_rpc, execute_rpc, publish_rpc = [], [], []
            try:
                self.call_all("prepare", bodies, telemetry=prepare_rpc)
                prepared = time.monotonic()
                execution = self.call_all("execute", [{"tx": tx} for _ in participants], telemetry=execute_rpc)
                filled = time.monotonic()
                filled_ns = time.monotonic_ns()
                expected = {o["op_id"]: o["n"] for o in plan["ops"]}
                actual = {r["op_id"]: r["bytes"] for w in execution for r in w["records"]
                          if r["role"] in ("target", "both")}
                if expected != actual: raise RuntimeError("actual target bytes differ from plan")
                verified = self.verify(tx) if verify_before_ready else None
                self.call_all("complete", [{"tx": tx, "verified": verified is not None} for _ in participants], telemetry=publish_rpc)
                ready = time.monotonic()
                ready_ns = time.monotonic_ns()
                result = {"tx": tx, "plan": plan, "execution": execution, "verification": verified,
                          "executor": plan["executor"],
                          "prepare_s": prepared-started, "execute_wall_s": filled-prepared,
                          "inventory_rpc_s": inventoried-started, "prepare_rpc_s": prepared-inventoried,
                          "epoch_snapshot_reused": reused, "control_mode": self.control_mode,
                          "rpc": {"inventory": inventory_rpc, "prepare": prepare_rpc,
                                  "execute": execute_rpc, "publish": publish_rpc},
                          "verify_and_publish_s": ready-filled, "coordinator_wall_s": ready-started,
                          "controller_timing": {"clock_domain": CONTROLLER_CLOCK_DOMAIN,
                              "load_begin_ns": started_ns, "all_targets_filled_ns": filled_ns,
                              "all_targets_published_ns": ready_ns},
                          "source_bytes": {}, "timing_scope": "coordinator wall; excludes model layout before handoff"}
                for op in plan["ops"]:
                    rid = op["replica_id"]
                    result["source_bytes"][rid] = result["source_bytes"].get(rid, 0)+op["n"]
                if not retain: self.finish(tx)
                return result
            except BaseException as exc:
                try:
                    self.call_all("abort", [{"tx": tx} for _ in participants])
                except Exception as cleanup:
                    raise RuntimeError(f"transfer failed: {exc}; abort also failed: {cleanup}") from exc
                finally:
                    self.active.pop(tx, None)
                raise

    def finish(self, tx):
        self.call_all("finish", [{"tx": tx} for _ in self.endpoints])
        self.active.pop(tx, None)

    def place(self, requests, policy="nearest", scenarios=None, extra_budget=8 << 30):
        with self.lock:
            workers, replicas = self.inventory()
            if self.planner_endpoint:
                planning = request(self.planner_endpoint, "plan_placement", {
                    "replicas": replicas, "requests": requests, "workers": workers, "profile": self.profile,
                    "policy": policy, "scenarios": scenarios, "extra_budget": extra_budget})
            else:
                planning = placement_plan(replicas, requests, workers, self.profile, policy, scenarios, extra_budget)
            targets = []
            for decision in planning["decisions"]:
                r = dict(decision["replica"])
                source = next(x for x in replicas if x["identity"] == r["identity"] and x["tp"] == r["tp"])
                r.update(key=r["id"], mode="replica", meta=source["meta"], index=source["index"])
                targets.append(r)
            result = self.transfer(self.solve_restore(replicas, targets, workers)) if targets else None
            return {"placement": planning, "transfer": result}

    def retire(self, replica_id):
        with self.lock:
            if self.active: raise RuntimeError("cannot change placement during an active restore")
            # Retirement uses exact byte intervals, identities and owners, not tensor manifests.
            _, replicas = self.inventory(compact=True)
            source = check_retirement(replicas, replica_id)
            return request(self.endpoints[source["worker"]], "retire", {"id": replica_id})

    def migrate(self, replica_id, target_id, worker, tier):
        with self.lock:
            _, replicas = self.inventory()
            old = next(r for r in replicas if r["id"] == replica_id)
            req = {k: old[k] for k in ("identity", "tp", "off", "n")}
            req.update(id=target_id, preferred_worker=worker, locations=[{"worker": worker, "tier": tier}])
            result = self.place([req], extra_budget=(old["n"]+4095)//4096*4096)
            self.retire(replica_id)
            return result
