"""Bound an original worker's lifetime and reuse frozen exact-owner cleanup."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[6]
HELPER = ROOT / "codex_coding/src/v0.1.23.3/task2/three_node_recovery/admission28/runtime_gate_r03.py"
HELPER_SHA = "d1ae5e31c3bf801d36011cbb9a6e911b400fd4e64c6689c8c742d4aa78d4f058"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--node", choices=["node28", "node30"], required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--startup-budget-s", type=float, default=60)
    parser.add_argument("--lifetime-s", type=float, default=300)
    args = parser.parse_args()
    if not 0 < args.startup_budget_s <= 60 or not args.startup_budget_s < args.lifetime_s <= 300:
        raise ValueError("invalid bounded worker deadline")
    if hashlib.sha256(HELPER.read_bytes()).hexdigest() != HELPER_SHA:
        raise ValueError("frozen cleanup helper changed")
    module_spec = importlib.util.spec_from_file_location("frozen_worker_cleanup", HELPER)
    gate = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(gate)
    plan = json.loads(args.plan.read_text())
    row = next(w for w in plan["workers"] if w["node"] == args.node)
    if plan["session"] != "minimal_calibration_r01" or not row.get("container_id") or gate.boot_id() != row.get("boot_id"):
        raise ValueError("worker plan session/boot/container binding missing or changed")
    out = args.out.resolve()
    expected_parent = ROOT / "codex_coding/results/v0.1.23.3/task2/minimal_calibration"
    if out.parent != expected_parent or not out.name.startswith("integration_worker_"):
        raise ValueError("require new integration_worker_* output")
    out.mkdir(parents=True, exist_ok=False)
    dump = Path(row["env"]["NCCL_TOPO_DUMP_FILE"])
    if dump == Path(row["env"]["NCCL_TOPO_FILE"]) or dump.exists():
        raise ValueError("dump must be a fresh file distinct from XML input")
    dump.parent.mkdir(parents=True, exist_ok=True)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    env = dict(os.environ, **row["env"])
    for name in row["unset_env"]:
        env.pop(name, None)
    namespace, boot = os.readlink("/proc/self/ns/pid"), gate.boot_id()
    stop = {"reason": None}
    def on_signal(signum, frame):
        stop["reason"] = "supervisor_signal_" + str(signum)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    plan_sha = hashlib.sha256(args.plan.read_bytes()).hexdigest()
    log_path, stop_path = out / "worker.log", out / "STOP.json"
    start = time.monotonic()
    owner = {}
    summary = dict(node=args.node, session=plan["session"], plan_sha256=plan_sha,
                   boot_id=boot, pid_namespace=namespace, expected_container_id=row["container_id"],
                   container_identity_verified_by="parent docker full-ID and current guard",
                   helper_sha256=HELPER_SHA, startup_ready=False, cgroup_samples=[],
                   physical_peak_verified=False)
    child = None
    try:
        with log_path.open("xb") as log:
            child = subprocess.Popen(row["argv"], env=env, cwd=ROOT,
                                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        owner = gate.proc(child.pid)
        if not owner.get("live") or owner["argv"][1:] != row["argv"][1:]:
            raise ValueError("child exact argv registration failed")
        gate.write(out / "worker.owner.json", dict(owner, boot_id=boot, pid_namespace=namespace,
                                                    plan_sha256=plan_sha, expected_container_id=row["container_id"]))
        print(json.dumps(dict(supervisor_pid=os.getpid(), child_pid=child.pid,
                              owner_file=str(out / "worker.owner.json"))), flush=True)
        relative = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
        cgroup = Path("/sys/fs/cgroup") / relative.lstrip("/")
        summary["cgroup_path"] = str(cgroup)
        while child.poll() is None:
            elapsed = time.monotonic()-start
            if not summary["startup_ready"]:
                with log_path.open("rb") as stream:
                    prefix = stream.read(65536)
                if b'"ready": true' in prefix:
                    summary.update(startup_ready=True, startup_ready_s=elapsed)
                    gate.write(out / "startup.ready.json", summary)
            if stop_path.exists():
                request = json.loads(stop_path.read_text())
                if request.get("plan_sha256") != plan_sha:
                    raise ValueError("stop marker belongs to another worker plan")
                stop["reason"] = "owned_stop_marker"
            if stop["reason"] or elapsed > args.lifetime_s:
                summary["stop_reason"] = stop["reason"] or "lifetime_deadline"
                break
            if not summary["startup_ready"] and elapsed > args.startup_budget_s:
                summary["stop_reason"] = "startup_deadline"
                break
            sample = dict(elapsed_s=elapsed)
            for name in ("memory.current", "memory.peak", "memory.max", "pids.current"):
                try:
                    sample[name] = (cgroup / name).read_text().strip()
                except (FileNotFoundError, PermissionError):
                    sample[name] = None
            summary["cgroup_samples"].append(sample)
            time.sleep(0.25)
    except BaseException as exc:
        summary["failure"] = repr(exc)
    finally:
        if child is not None:
            try:
                summary["cleanup"] = gate.cleanup(child, owner, boot, namespace)
            except BaseException as exc:
                summary["cleanup"] = dict(passed=False, error=repr(exc), signals_unknown=True)
            summary["child_returncode"] = child.poll()
        summary["elapsed_s"] = time.monotonic()-start
        summary["cgroup_peak_scope"] = "sampled values are lower bounds when kernel memory.peak is absent; parent must bind cgroup path"
        gate.write(out / "supervisor.summary.json", summary)
    passed = bool(summary.get("startup_ready") and not summary.get("failure")
                  and summary.get("stop_reason") == "owned_stop_marker" and summary.get("cleanup", {}).get("passed"))
    print(json.dumps(dict(passed=passed, out=str(out), cleanup=summary.get("cleanup"))), flush=True)
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
