"""Reuse the original finite-session guard/readiness/STOP plumbing for four manual layouts."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import importlib.util
import json
from pathlib import Path
import shlex
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[6]
HERE = Path(__file__).resolve().parent
MIN = ROOT / "codex_coding/results/v0.1.23.3/task2/minimal_calibration"
RESULTS = ROOT / "codex_coding/results/v0.1.23.3/task2/layout_validation"
SOURCE = ROOT / "codex_coding/src/v0.1.23.3/task2/minimal_calibration"
module_spec = importlib.util.spec_from_file_location("original_control_plumbing", SOURCE / "measure/orchestrate_integration.py")
old = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(old)
NODES = ("node28", "node30")
GUARD_DIR = MIN / "measure_integration_deploy_r01"
BASE_PLAN = MIN / "integration_actual_plan_r02/launch_plan.json"
WORKER_NAME = "integration_worker_{node}_layout_r01"
DUMP_NAME = "integration_layout_worker_session_r01"


def guards():
    return {node: json.loads((GUARD_DIR / (node + ".latest.guard.spec.json")).read_text()) for node in NODES}


def fresh_guards(out, phase):
    expected = guards()
    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(lambda node: old.checked_guard(node, phase, out, expected[node]), NODES))


def metadata(worker, plan, out, name, final=False):
    remote = ["python3", "-", "--binding", json.dumps(worker), "--plan", str(plan)]
    if final:
        remote.extend(["--worker-out", str(MIN / WORKER_NAME.format(node=worker["node"]))])
    process, stdout = old.record(old.ssh(worker["node"], remote), out / name, 20,
                                 (HERE / "metadata_readback.py").read_bytes())
    if process["returncode"] != 0:
        raise RuntimeError("metadata readback failed: " + worker["node"])
    body = json.loads(stdout)
    old.write(out / name / "stdout.json", body)
    if body["hash_returncode"] != 0:
        raise ValueError("frozen file hash readback failed")
    return old.ref(out / name / "stdout.json")


def prepare(args):
    args.out.mkdir(parents=True, exist_ok=False)
    base = json.loads(BASE_PLAN.read_text())
    plan = copy.deepcopy(base)
    changes = []
    for worker in plan["workers"]:
        previous = worker["env"]["NCCL_TOPO_DUMP_FILE"]
        worker["env"]["NCCL_TOPO_DUMP_FILE"] = str(MIN / DUMP_NAME / (worker["node"] + ".runtime.dump.xml"))
        index = worker["argv"].index("--master") + 1
        previous_master = worker["argv"][index]
        worker["argv"][index] = "12.11.3.28:29972"
        changes.extend([{"node": worker["node"], "field": "NCCL_TOPO_DUMP_FILE", "before": previous,
                         "after": worker["env"]["NCCL_TOPO_DUMP_FILE"]},
                        {"node": worker["node"], "field": "argv.--master", "before": previous_master,
                         "after": worker["argv"][index]}])
    plan_path = args.out / "launch_plan.json"
    old.write(plan_path, plan)
    commands = []
    for worker in plan["workers"]:
        node = worker["node"]
        remote = ["docker", "exec", "-e", "PYTHONDONTWRITEBYTECODE=1", worker["container_id"], "python3",
                  str(SOURCE / "integration/supervise_worker.py"), "--plan", str(plan_path), "--node", node,
                  "--out", str(MIN / WORKER_NAME.format(node=node)), "--startup-budget-s", "60", "--lifetime-s", "300"]
        argv = ["timeout", "--signal=TERM", "--kill-after=5s", "330s", *old.ssh(node, remote)]
        old.write(args.out / (node + ".worker.command.json"), {"argv": argv})
        (args.out / (node + ".worker.command.sh")).write_text(shlex.join(argv) + "\n")
        deploy = ["docker", "exec", "-i", worker["container_id"], "bash", "-c",
                  "set -C\nmkdir -p " + shlex.quote(str(plan_path.parent)) + "\ncat > " + shlex.quote(str(plan_path))]
        old.write(args.out / (node + ".deploy.command.json"), {"argv": old.ssh(node, deploy),
                  "stdin_file": str(plan_path), "required_parent_guard_before_write": True})
        commands.append({"node": node, "argv": argv})
    summary = {"base_launch_plan_ref": old.ref(BASE_PLAN), "launch_plan_ref": old.ref(plan_path),
               "changes": changes, "commands": commands, "GPU_launches": 0,
               "source_ref": old.ref(Path(__file__)), "readiness_passed": False, "remote_writes": 0}
    old.write(args.out / "manifest.json", summary)
    if args.readonly:
        summary["preflight_guard_refs"] = fresh_guards(args.out, "preflight")
        bindings = {node: json.loads((args.out / ("preflight_" + node) / "stdout.json").read_text()) for node in NODES}
        for worker in base["workers"]:
            node = worker["node"]
            if bindings[node]["container_cgroup"]["files"].get("pids.current") != "1":
                raise ValueError("owned container is not quiet before launch")
            remote = ["python3", "-", "--guard-source", "/tmp/fluidckpt_minimal_calibration_r01_bootstrap/measurement_host.source.py",
                      "--spec", "/tmp/fluidckpt_minimal_calibration_r01_bootstrap/" + node + ".integration.guard.spec.json"]
            process, stdout = old.record(old.ssh(node, remote), args.out / ("refresh_" + node), 35,
                                         (SOURCE / "measure/refresh_host.py").read_bytes())
            if process["returncode"] != 0:
                raise RuntimeError("refresh failed")
            old.write(args.out / ("refresh_" + node) / "stdout.json", json.loads(stdout))
            for port in (34500, 29972):
                remote = ["python3", "-", "--node", node, "--port", str(port)]
                process, stdout = old.record(old.ssh(node, remote), args.out / ("listener_" + node + "_" + str(port)), 12,
                                             (SOURCE / "measure/listener_host.py").read_bytes())
                body = json.loads(stdout) if process["returncode"] == 0 else {}
                old.write(args.out / ("listener_" + node + "_" + str(port)) / "stdout.json", body)
                if body.get("listener_free") is not True:
                    raise ValueError("parent-selected port is occupied or unproven")
            metadata(worker, BASE_PLAN, args.out, "metadata_" + node)
        summary["readiness_passed"] = True
        old.write(args.out / "manifest.json", summary)
    print(json.dumps({"out": str(args.out), "readiness_passed": summary["readiness_passed"], "GPU_launches": 0}))


def controller(args):
    plan = json.loads(args.plan.read_text())
    spec = json.loads(args.spec.read_text())
    if spec["session"] != plan["session"] or spec["session"] != "minimal_calibration_r01" or spec["block_sizes_mib"] != [16]:
        raise ValueError("session or two16MiB scope differs")
    args.out.mkdir(parents=True, exist_ok=False)
    summary = {"plan_ref": old.ref(args.plan), "original_spec_ref": old.ref(args.spec),
               "source_ref": old.ref(Path(__file__)), "application_passed": False, "status": "prepared", "stop": {}}
    old.write(args.out / "summary.json", summary)
    frozen_plan = old.ref(args.plan)
    frozen_runner = old.ref(args.runner)
    start = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            summary["ready_refs"] = list(pool.map(lambda w: old.wait_ready(w, plan["session"], args.out), plan["workers"]))
        current = fresh_guards(args.out, "before_mesh")
        mesh = copy.deepcopy(spec)
        mesh.update(current_guard_refs=current, actual_worker_launch_plan_ref=frozen_plan,
                    actual_launch_plan_ref=frozen_plan)
        for worker in mesh["workers"]:
            worker["baseline_guard_ref"] = old.ref(args.baseline / ("baseline_" + worker["node"]) / "stdout.json")
        mesh_path = args.out / "fresh.mesh.spec.json"
        old.write(mesh_path, mesh)
        mesh_out = MIN / "integration_layout_mesh_r01"
        argv = [sys.executable, str(Path(__file__).resolve()), "mesh-step", "--spec", str(mesh_path), "--plan", str(args.plan), "--out", str(mesh_out)]
        process, _ = old.record(argv, args.out / "mesh_controller", 90)
        summary["mesh_process"] = process
        result = json.loads((mesh_out / "execution.json").read_text())
        summary["mesh_ref"] = old.ref(mesh_out / "execution.json")
        if process["returncode"] != 0 or result.get("status") != "mesh_ready_budget_pending":
            raise RuntimeError("original mesh/warm failed")
        warmed = fresh_guards(args.out, "post_warm")
        app = copy.deepcopy(mesh)
        app.update(current_guard_refs=warmed, mesh_initialized=True, pair_warmed=True)
        for worker in app["workers"]:
            worker["post_mesh_guard_ref"] = next(r for r in warmed if "_" + worker["node"] + "/" in r["path"])
        app_path = args.out / "fresh.app.spec.json"
        old.write(app_path, app)
        if time.monotonic() - start > 160:
            raise TimeoutError("insufficient 300s worker lifetime remaining")
        if old.ref(args.plan) != frozen_plan or old.ref(args.runner) != frozen_runner:
            raise ValueError("frozen launch plan or runner changed during session")
        app_out = RESULTS / "execution_r01"
        argv = [sys.executable, str(args.runner), "--spec", str(app_path), "--out", str(app_out), "--execute"]
        process, _ = old.record(argv, args.out / "app_controller", 90)
        summary["app_process"] = process
        if not (app_out / "execution.json").exists():
            raise RuntimeError("four-layout application artifact missing")
        result = json.loads((app_out / "execution.json").read_text())
        summary["app_ref"] = old.ref(app_out / "execution.json")
        summary["application_status"] = result.get("status")
        if process["returncode"] != 0:
            raise RuntimeError("application failed or budget rejected; raw result retained")
        if result.get("status") == "budget_rejected_before_synth":
            raise ValueError("application budget rejected before allocation")
        cases = result.get("cases", [])
        if result.get("status") != "passed" or len(cases) != 1 or set(cases[0].get("layouts", {})) != {"A", "B", "C", "D"}:
            raise ValueError("application lacks four complete passed layouts")
        case = cases[0]
        if case.get("failure") or case.get("cleanup_errors") or case.get("owned_replicas_remaining") or case.get("postflight_error"):
            raise ValueError("application cleanup failed")
        for name, layout in case["layouts"].items():
            verification = layout.get("verification", {})
            if (layout.get("status") != "passed" or layout.get("cleanup_errors")
                    or verification.get("passed") is not True or verification.get("checked_bytes") != 33554432):
                raise ValueError("layout not passed: " + name)
        summary.update(status="application_completed_pending_independent_review", application_passed=True)
    except BaseException as exc:
        summary.update(status="failed", failure=repr(exc))
    finally:
        summary["stop"] = stop_workers(plan, frozen_plan["sha256"], args.out)
        summary.update(elapsed_s=time.monotonic() - start, stop_cleanup_verified=False)
        old.write(args.out / "summary.json", summary)
    print(json.dumps(summary))
    if not summary["application_passed"]:
        raise SystemExit(2)


def stop_workers(plan, digest, out):
    result = {}
    payload = (json.dumps({"plan_sha256": digest}) + "\n").encode()
    for worker in plan["workers"]:
        node = worker["node"]
        target = MIN / WORKER_NAME.format(node=node) / "STOP.json"
        remote = ["docker", "exec", "-i", worker["container_id"], "tee", str(target)]
        try:
            process, _ = old.record(old.ssh(node, remote), out / ("stop_" + node), 12, payload)
            result[node] = process
        except BaseException as exc:
            result[node] = {"failure": repr(exc)}
    return result


def mesh_step(args):
    if args.out != MIN / "integration_layout_mesh_r01":
        raise ValueError("original mesh step requires the unique permitted minimal output")
    sys.path.insert(0, str(ROOT / "main_lib"))
    sys.path.insert(0, str(SOURCE / "integration"))
    source = SOURCE / "integration/run_session.py"
    module_spec = importlib.util.spec_from_file_location("original_mesh_runner", source)
    runner = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(runner)
    spec = json.loads(args.spec.read_text())
    spec["_source_path"] = str(args.spec.resolve())
    args.out.mkdir(parents=True, exist_ok=False)
    old.write(args.out / "registration.json", {"actual_launch_plan_ref": old.ref(args.plan),
              "original_mesh_runner_ref": old.ref(source), "spec_ref": old.ref(args.spec)})
    result = runner.execute(spec, args.out, mesh_only=True)
    old.write(args.out / "controller.return.json", {"returned": True, "status": result.get("status")})
    if result.get("status") != "mesh_ready_budget_pending":
        raise SystemExit(2)


def parent_run(args):
    if not args.execute:
        raise ValueError("parent-run requires explicit --execute; this starts the authorized GPU workers")
    plan = json.loads(args.plan.read_text())
    plan_ref = old.ref(args.plan)
    args.out.mkdir(parents=True, exist_ok=False)
    summary = {"plan_ref": plan_ref, "runner_ref": old.ref(args.runner), "status": "prelaunch", "workers": {}}
    summary["source_refs"] = [old.ref(Path(__file__)), old.ref(HERE / "metadata_readback.py"),
             old.ref(HERE / "exact_owned_readonly.py"), old.ref(SOURCE / "measure/orchestrate_integration.py"),
             old.ref(SOURCE / "integration/supervise_worker.py"), old.ref(SOURCE / "integration/run_session.py"),
             old.ref(SOURCE / "integration/build_contract.py")]
    old.write(args.out / "summary.json", summary)
    baseline_out = RESULTS / "operations_baseline_r01"
    baseline_out.mkdir(parents=True, exist_ok=False)
    fresh_guards(baseline_out, "baseline")
    for worker in plan["workers"]:
        node = worker["node"]
        body = json.loads((baseline_out / ("baseline_" + node) / "stdout.json").read_text())
        guard = guards()[node]
        if (worker["container_id"], worker["container_started_at"], worker["boot_id"]) != (guard["expected_container_id"], guard["expected_container_started"], guard["boot_id"]):
            raise ValueError("launch differs from frozen guard identity")
        if body["container_cgroup"]["files"]["pids.current"] != "1":
            raise ValueError("owned container is not quiet before worker startup")
        for port in (34500, 29972):
            process, stdout = old.record(old.ssh(node, ["python3", "-", "--node", node, "--port", str(port)]),
                        args.out / ("prelaunch_port_" + node + "_" + str(port)), 12,
                        (SOURCE / "measure/listener_host.py").read_bytes())
            if process["returncode"] != 0 or json.loads(stdout).get("listener_free") is not True:
                raise ValueError("prelaunch selected port no longer free")
        deployment = json.loads((args.plan.parent / (node + ".deploy.command.json")).read_text())
        process, _ = old.record(deployment["argv"], args.out / ("deploy_" + node), 20, args.plan.read_bytes())
        if process["returncode"] != 0:
            raise RuntimeError("noclobber new launch plan deployment failed")
        metadata(worker, args.plan, args.out, "deployed_" + node)
        readback = json.loads((args.out / ("deployed_" + node) / "stdout.json").read_text())
        actual = {line.split(maxsplit=1)[1]: line.split(maxsplit=1)[0] for line in readback["hash_stdout"].splitlines()}
        if actual.get(str(args.plan)) != plan_ref["sha256"]:
            raise ValueError("independent deployed launch plan readback differs")
    if old.ref(args.plan) != plan_ref:
        raise ValueError("plan changed before startup")
    if old.ref(args.runner) != summary["runner_ref"] or any(old.ref(Path(ref["path"])) != ref for ref in summary["source_refs"]):
        raise ValueError("frozen local runtime source changed before startup")
    control_out = RESULTS / "operations_control_r01"
    futures = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            for worker in plan["workers"]:
                node = worker["node"]
                command = json.loads((args.plan.parent / (node + ".worker.command.json")).read_text())["argv"]
                futures[node] = pool.submit(old.record, command, args.out / ("worker_" + node), 350)
            command = [sys.executable, str(Path(__file__).resolve()), "controller", "--plan", str(args.plan),
                       "--spec", str(args.spec), "--runner", str(args.runner), "--baseline", str(baseline_out),
                       "--out", str(control_out)]
            process, _ = old.record(command, args.out / "controller", 270)
            summary["controller_process"] = process
        finally:
            # The outer parent ensures STOP even if controller setup failed before its own finally.
            summary["fallback_stop"] = stop_workers(plan, plan_ref["sha256"], args.out)
            for node, future in futures.items():
                summary["workers"][node] = future.result()[0]
            old.write(args.out / "summary.json", summary)
    collect_args = copy.copy(args)
    collect_args.out = RESULTS / "operations_collect_r01"
    collect(collect_args)
    summary["status"] = "completed_pending_independent_review" if summary.get("controller_process", {}).get("returncode") == 0 and all(v["returncode"] == 0 for v in summary["workers"].values()) else "failed"
    summary["collection_out"] = str(collect_args.out)
    old.write(args.out / "summary.json", summary)
    if summary["status"] == "failed":
        raise SystemExit(2)


def collect(args):
    args.out.mkdir(parents=True, exist_ok=False)
    plan = json.loads(args.plan.read_text())
    fresh_guards(args.out, "after_stop")
    for worker in plan["workers"]:
        node = worker["node"]
        metadata(worker, args.plan, args.out, "metadata_" + node, final=True)
        remote = ["docker", "exec", "-i", worker["container_id"], "python3", "-", "--owner",
                  str(MIN / WORKER_NAME.format(node=node) / "worker.owner.json")]
        process, stdout = old.record(old.ssh(node, remote), args.out / ("exact_owner_" + node), 15,
                                      (HERE / "exact_owned_readonly.py").read_bytes())
        if stdout:
            old.write(args.out / ("exact_owner_" + node) / "stdout.json", json.loads(stdout))
        if process["returncode"] != 0:
            raise RuntimeError("fresh exact owned process absence/group empty failed")
        stage = "/tmp/fluidckpt_three_node/minimal_measure_r01_n" + node[-2:] + "/project/"
        remote_root = stage + str(MIN.relative_to(ROOT))
        argv = old.ssh(node, ["tar", "-C", remote_root, "-cf", "-", WORKER_NAME.format(node=node), DUMP_NAME])
        process, stdout = old.record(argv, args.out / ("collect_" + node), 20)
        if process["returncode"] != 0:
            raise RuntimeError("owned artifact collection failed")
        archive = args.out / ("collect_" + node) / "worker.tar"
        archive.write_bytes(stdout)
        with tarfile.open(archive) as handle:
            for member in handle.getmembers():
                if member.issym() or member.islnk() or member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise ValueError("unsafe owned archive entry")
            handle.extractall(args.out / ("collect_" + node) / "raw", filter="data")
    print(json.dumps({"out": str(args.out), "GPU_launches": 0, "signals": 0}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "baseline", "controller", "collect", "mesh-step", "parent-run"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--readonly", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--runner", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    args.out = args.out.resolve()
    for name in ("plan", "spec", "runner", "baseline"):
        if getattr(args, name) is not None:
            setattr(args, name, getattr(args, name).resolve())
    if args.mode == "mesh-step":
        mesh_step(args)
        return
    if args.out.parent != RESULTS or not args.out.name.startswith("operations_"):
        raise ValueError("require fresh layout_validation/operations_* output")
    if args.mode == "prepare":
        prepare(args)
    elif args.mode == "baseline":
        args.out.mkdir(parents=True, exist_ok=False)
        fresh_guards(args.out, "baseline")
    elif args.mode == "controller":
        controller(args)
    elif args.mode == "parent-run":
        parent_run(args)
    else:
        collect(args)


if __name__ == "__main__":
    main()
