"""Parent-run fresh guards, existing mesh/application controller, and matching STOP."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / "codex_coding/results/v0.1.23.3/task2/minimal_calibration"
INTEGRATION = ROOT / "codex_coding/src/v0.1.23.3/task2/minimal_calibration/integration"
OPTIONS = ["-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ConnectionAttempts=1",
           "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no", "-o", "ServerAliveInterval=3", "-o", "ServerAliveCountMax=1"]


def ref(path):
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def write(path, body):
    path.write_text(json.dumps(body, indent=2) + "\n")


def ssh(node, remote):
    return ["ssh", *OPTIONS, "-o", "Hostname=12.11.3." + node[-2:], node, shlex.join(remote)]


def record(argv, directory, timeout, input_bytes=None):
    directory.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    data = {"argv": argv, "timeout_s": timeout}
    try:
        done = subprocess.run(argv, input=input_bytes, capture_output=True, timeout=timeout)
        stdout, stderr = done.stdout, done.stderr
        data.update(returncode=done.returncode, timed_out=False)
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = exc.stdout or b"", exc.stderr or b""
        data.update(returncode=None, timed_out=True, failure=repr(exc),
                    timeout_scope="only this new local controller/SSH client; worker cleanup uses STOP")
    except OSError as exc:
        stdout, stderr = b"", b""
        data.update(returncode=None, timed_out=False, failure=repr(exc))
    (directory / "stdout.txt").write_bytes(stdout)
    (directory / "stderr.txt").write_bytes(stderr)
    data["elapsed_s"] = time.monotonic() - start
    write(directory / "command.json", data)
    return data, stdout


def checked_guard(node, phase, output, guard_spec):
    remote = ["python3", "/tmp/fluidckpt_minimal_calibration_r01_bootstrap/measurement_host.source.py", "guard",
              "/tmp/fluidckpt_minimal_calibration_r01_bootstrap/" + node + ".integration.guard.spec.json"]
    directory = output / (phase + "_" + node)
    process, stdout = record(ssh(node, remote), directory, 35)
    if process["returncode"] != 0:
        raise RuntimeError("guard failed: " + node + "/" + phase)
    body = json.loads(stdout)
    write(directory / "stdout.json", body)
    if not all(body.get("container_identity_checks", {}).get(key) is True for key in ("id", "started", "image", "running")):
        raise ValueError("guard container identity failed")
    if body["boot_id"] != guard_spec["boot_id"] or body["container"]["id"] != guard_spec["expected_container_id"]:
        raise ValueError("guard source belongs to another container or boot")
    if not body.get("source_assets") or not all(v.get("sha256") == v.get("expected_sha256") for v in body["source_assets"].values()):
        raise ValueError("staged source guard failed")
    expected_assets = {row["relative_path"]: row["sha256"] for row in guard_spec["assets"]}
    actual_assets = {path: row.get("sha256") for path, row in body["source_assets"].items()}
    if actual_assets != expected_assets:
        raise ValueError("remote guard source set/SHA differs from the local frozen guard spec")
    if not all(v.get("owner_matches") is True for v in body.get("keeper_process_discovery", [])):
        raise ValueError("retained owner changed")
    catalogs = {v["endpoint"]: v.get("catalog_sha256") for v in body.get("keepers", [])}
    if catalogs != guard_spec.get("expected_keeper_catalogs", {}):
        raise ValueError("retained catalog changed")
    events = body["container_cgroup"]["files"]["memory.events"]
    if not isinstance(events, str) or "oom 0" not in events or "oom_kill 0" not in events:
        raise ValueError("container OOM evidence failed")
    return ref(directory / "stdout.json")


def wait_ready(worker, session, output):
    directory = output / ("ready_" + worker["node"])
    directory.mkdir(exist_ok=False)
    endpoint = worker["endpoint"]
    if endpoint != "http://12.11.3." + worker["node"][-2:] + ":34500":
        raise ValueError("readiness endpoint outside the new owned pair")
    start = time.monotonic()
    evidence = {"node": worker["node"], "endpoint": endpoint, "deadline_s": 60, "attempts": [],
                "operation": "GET /inventory?compact=1; existing worker metadata, no application allocation"}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() - start < 60:
        row = {"elapsed_s": time.monotonic() - start}
        try:
            with opener.open(endpoint + "/inventory?compact=1", timeout=min(2, 60-row["elapsed_s"])) as response:
                raw = response.read((1 << 20) + 1)
            if len(raw) > 1 << 20:
                raise ValueError("inventory exceeds bounded response")
            body = json.loads(raw)
            expected = (worker["node"], session, worker["numa"], worker["logical_gpu"])
            actual = tuple(body.get(k) for k in ("node", "session", "numa", "gpu"))
            if actual != expected or body.get("replicas") or body.get("transactions"):
                raise ValueError("readiness identity or empty-session scope differs")
            row.update(passed=True, inventory_sha256=hashlib.sha256(raw).hexdigest())
            evidence["attempts"].append(row)
            (directory / "inventory.raw.json").write_bytes(raw)
            evidence.update(passed=True, elapsed_s=time.monotonic()-start)
            write(directory / "summary.json", evidence)
            return ref(directory / "summary.json")
        except ValueError as exc:
            row.update(passed=False, failure=repr(exc))
            evidence["attempts"].append(row)
            write(directory / "summary.json", evidence)
            raise
        except Exception as exc:
            row.update(passed=False, retryable_startup_error=repr(exc))
            evidence["attempts"].append(row)
            write(directory / "summary.json", evidence)
        time.sleep(min(.25, max(0, 60-(time.monotonic()-start))))
    evidence.update(passed=False, elapsed_s=time.monotonic()-start, failure="startup readiness deadline")
    write(directory / "summary.json", evidence)
    raise TimeoutError("worker HTTP startup readiness expired: " + worker["node"])


def step(args):
    sys.path.insert(0, str(ROOT / "main_lib"))
    sys.path.insert(0, str(INTEGRATION))
    module_spec = importlib.util.spec_from_file_location("existing_integration_controller", INTEGRATION / "run_session.py")
    runner = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(runner)
    spec = json.loads(args.spec.read_text())
    spec["_source_path"] = str(args.spec.resolve())
    if args.out.parent.resolve() != RESULTS or not args.out.name.startswith("integration_"):
        raise ValueError("controller step requires a fresh integration_* result directory")
    args.out.mkdir(parents=True, exist_ok=False)
    write(args.out / "registration.json", {"spec_ref": ref(args.spec), "actual_worker_launch_plan_ref": ref(args.plan),
                                           "existing_runner_ref": ref(INTEGRATION / "run_session.py"), "step": args.step})
    result = runner.execute(spec, args.out, mesh_only=args.step == "mesh")
    write(args.out / "controller.return.json", {"returned": True, "status": result.get("status"),
          "actual_worker_launch_plan_ref": ref(args.plan), "automatic_worker_launch": False})
    if args.step == "mesh" and result.get("status") != "mesh_ready_budget_pending":
        raise SystemExit(2)
    if args.step == "app" and (result.get("status") == "budget_rejected_before_synth" or
                                result.get("mesh_resource_budget", {}).get("passed") is not True):
        raise SystemExit(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-contract", type=Path)
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--guard-spec-dir", type=Path)
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run", default="r02")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--step", choices=("mesh", "app"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.out = args.out.resolve()
    if args.step:
        step(args)
        return
    if not args.original_contract or not args.guard_spec_dir or not args.baseline_dir or args.run != "r02":
        parser.error("require the original contract, deployed guard specs, baseline evidence and run r02")
    plan = json.loads(args.plan.read_text())
    spec = json.loads(args.original_contract.read_text())
    if plan["session"] != spec["session"] or spec["block_sizes_mib"] != [16]:
        raise ValueError("session/two16MiB scope differs")
    guards = {node: json.loads((args.guard_spec_dir / (node + ".latest.guard.spec.json")).read_text()) for node in ("node28", "node30")}
    for worker in plan["workers"]:
        guard = guards[worker["node"]]
        if (worker["container_id"], worker["container_started_at"], worker["boot_id"]) != (
                guard["expected_container_id"], guard["expected_container_started"], guard["boot_id"]):
            raise ValueError("launch plan and guard identities differ")
    args.out.mkdir(parents=True, exist_ok=False)
    summary = {"original_contract_ref": ref(args.original_contract), "actual_worker_launch_plan_ref": ref(args.plan),
               "source_ref": ref(Path(__file__)), "worker_launch_by_parent": True, "execute": args.execute,
               "application_passed": False, "formal_performance_ready": False, "status": "prepared", "stop": {}}
    write(args.out / "summary.json", summary)
    if not args.execute:
        print(json.dumps({"out": str(args.out), "prepared": True, "GPU_launches": 0}))
        return
    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            summary["worker_ready_refs"] = list(pool.map(lambda worker: wait_ready(worker, plan["session"], args.out), plan["workers"]))
        with ThreadPoolExecutor(max_workers=2) as pool:
            references = list(pool.map(lambda node: checked_guard(node, "before_mesh", args.out, guards[node]), guards))
        mesh_spec = copy.deepcopy(spec)
        mesh_spec["current_guard_refs"] = references
        mesh_spec["actual_worker_launch_plan_ref"] = ref(args.plan)
        for binding in mesh_spec["workers"]:
            baseline = args.baseline_dir / ("measure_guard_before_integration_r02_" + binding["node"] + "_r01/stdout.json")
            binding["baseline_guard_ref"] = ref(baseline)
        mesh_path = args.out / "fresh.mesh.spec.json"
        write(mesh_path, mesh_spec)
        mesh_out = RESULTS / ("integration_mesh_" + args.run)
        argv = [sys.executable, str(Path(__file__).resolve()), "--step", "mesh", "--spec", str(mesh_path), "--plan", str(args.plan), "--out", str(mesh_out)]
        process, _ = record(argv, args.out / "mesh_controller", 90)
        summary["mesh_process"] = process
        if process["returncode"] != 0 or not (mesh_out / "execution.json").exists():
            raise RuntimeError("mesh controller failed; raw stderr/rc preserved")
        mesh_result = json.loads((mesh_out / "execution.json").read_text())
        summary["mesh_execution_ref"] = ref(mesh_out / "execution.json")
        if mesh_result.get("status") != "mesh_ready_budget_pending":
            raise ValueError("mesh runner return is not mesh-ready evidence")
        with ThreadPoolExecutor(max_workers=2) as pool:
            warmed = list(pool.map(lambda node: checked_guard(node, "post_warm", args.out, guards[node]), guards))
        app_spec = copy.deepcopy(mesh_spec)
        app_spec.update(current_guard_refs=warmed, mesh_initialized=True, pair_warmed=True)
        for binding in app_spec["workers"]:
            binding["post_mesh_guard_ref"] = next(r for r in warmed if "_" + binding["node"] + "/" in r["path"])
        app_path = args.out / "fresh.app.spec.json"
        write(app_path, app_spec)
        if time.monotonic() - started > 160:
            raise TimeoutError("insufficient bounded worker lifetime remains for application")
        app_out = RESULTS / ("integration_product_" + args.run)
        argv = [sys.executable, str(Path(__file__).resolve()), "--step", "app", "--spec", str(app_path), "--plan", str(args.plan), "--out", str(app_out)]
        process, _ = record(argv, args.out / "app_controller", 90)
        summary["app_process"] = process
        if not (app_out / "execution.json").exists():
            raise RuntimeError("application result missing; raw stderr/rc preserved")
        app_result = json.loads((app_out / "execution.json").read_text())
        summary["app_execution_ref"] = ref(app_out / "execution.json")
        summary["application_status"] = app_result.get("status")
        if app_result.get("status") == "budget_rejected_before_synth":
            summary["status"] = "budget_rejected_before_synth"
        elif process["returncode"] != 0:
            raise RuntimeError("application controller failed")
        else:
            cases = app_result.get("cases", [])
            if len(cases) != 1 or set(cases[0].get("layouts", {})) != {"source_concentrated", "target_local_distributed"} or cases[0].get("failure") or cases[0].get("cleanup_errors") or cases[0].get("owned_replicas_remaining") or cases[0].get("postflight_error"):
                raise ValueError("runner exit0 lacks both complete layouts and cleanup evidence")
            summary.update(status="application_completed_pending_independent_review", application_passed=True)
    except BaseException as exc:
        summary.update(status="failed", failure=repr(exc))
    finally:
        payload = (json.dumps({"plan_sha256": ref(args.plan)["sha256"]}) + "\n").encode()
        for worker in plan["workers"]:
            node = worker["node"]
            target = RESULTS / ("integration_worker_" + node + "_" + args.run) / "STOP.json"
            remote = ["docker", "exec", "-i", worker["container_id"], "tee", str(target)]
            result, _ = record(ssh(node, remote), args.out / ("stop_" + node), 12, payload)
            summary["stop"][node] = result
        summary["elapsed_s"] = time.monotonic() - started
        summary["stop_cleanup_verified"] = False
        write(args.out / "summary.json", summary)
    print(json.dumps({"out": str(args.out), "status": summary["status"], "application_passed": summary["application_passed"],
                      "cleanup_requires_supervisor_summary_and_fresh_exact_audit": True}))
    if not summary["application_passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
