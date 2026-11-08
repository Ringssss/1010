"""Coordinate actual upstream Store gate/full runs and clean only this run's container."""
import argparse
from concurrent.futures import Future
import hashlib
import json
from pathlib import Path
import resource
import shlex
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "main_lib"))
sys.path.insert(0, str(ROOT / "codex_coding/src/v0.1.23.3/task2/basic_scenarios"))
from runtime_support import (generate, http, load_state, retained_inventory,
                             select_base, source_signature)
from fluidckpt.benchmarks.serverless_adapter import UPSTREAM_SHA, atomic_json
from fluidckpt.serving.measure import summarize
from full_protocol import check_prepared, freeze_files, frozen_rank_hashes, parse_meminfo


def no_core():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main():
    no_core()
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["gate", "full"], required=True)
    p.add_argument("--session", type=Path, default=ROOT / "codex_coding/results/v0.1.23.3/task2/basic_scenarios/qwen_bootstrap_r01/session.json")
    p.add_argument("--run", required=True)
    p.add_argument("--prepared-data", type=Path)
    p.add_argument("--node", default="node29")
    p.add_argument("--timeout", type=float, default=600)
    p.add_argument("--frozen-source", type=Path, default=ROOT / "codex_coding/results/v0.1.23.3/task2/basic_scenarios/s2_r01/summary.json")
    a = p.parse_args()
    if not a.run.startswith("serverless_") or "/" in a.run:
        raise ValueError("run must be one new serverless_* directory name")
    out = ROOT / "codex_coding/results/v0.1.23.3/task2/baseline_loading" / a.run
    out.mkdir(parents=True, exist_ok=False)
    state = load_state(a.session)
    slot = state["slots"]["target_b"]
    model = state["models"]["B"]
    if slot.get("model") != "B" or len(slot["workers"]) != 2 or len(set(slot["workers"])) != 2:
        raise ValueError("target_b must be one unique TP2 slot for the frozen B model")
    if sorted(s["tp"] for s in model["sources"]) != [0, 1]:
        raise ValueError("source TP ranks must be unique 0 and 1")
    members = [state["members"][wid] for wid in slot["workers"]]
    if len({member[0] for member in members}) != 1 or sorted(member[1] for member in members) != [0, 1]:
        raise ValueError("target TP2 must use one host's unique GPU0 and GPU1")
    if a.mode == "full" and a.node != state["members"][slot["workers"][0]][0]:
        raise ValueError("adapter and target engine must share a host")
    name = "zjn-" + a.run.replace("_", "-")
    script_dir = Path(__file__).parent
    result = {"status": "running", "mode": a.mode, "upstream_sha": UPSTREAM_SHA,
              "container": name, "node": a.node, "commands": [], "cleanup": {}}
    if a.mode == "full":
        result["runtime_cache_state"] = "uncontrolled_unmeasured; direct-I/O attempt with upstream fallback"
    original_command = None
    process = None
    resume = None
    created = False
    active = False

    def save():
        atomic_json(out / "summary.json", result)

    def host_argv(command):
        if a.node == "node27":
            return command
        return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", a.node, shlex.join(command)]

    def host(command, timeout=120, check=True):
        argv = host_argv(command)
        value = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, preexec_fn=no_core)
        result["commands"].append({"argv": argv, "returncode": value.returncode,
                                   "stdout": value.stdout, "stderr": value.stderr})
        save()
        if check and value.returncode:
            raise RuntimeError(f"host command failed: {command[0]}")
        return value.stdout.strip()

    def wait_path(path, deadline):
        while not path.exists():
            if process is not None and process.poll() is not None:
                raise RuntimeError("loader exited before required artifact; inspect loader.log")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"artifact timeout: {path.name}")
            time.sleep(.05)

    try:
        workers, replicas = retained_inventory(state)
        select_base(model, replicas)
        result["protected_before"] = source_signature(replicas)
        result["inventory_before"] = workers
        if a.mode == "full":
            if not a.prepared_data:
                raise ValueError("full run requires prepared-data metadata")
            prepared = json.loads(a.prepared_data.read_text())
            frozen_hashes = frozen_rank_hashes(a.frozen_source, model)
            check_prepared(prepared, model, frozen_hashes)
            result["frozen_rank_sha256"] = frozen_hashes
            result["code_sha256_before"] = freeze_files(ROOT, (a.session, a.prepared_data, a.frozen_source))
            memory = parse_meminfo(host(["cat", "/proc/meminfo"]))
            result["host_memory_admission"] = {"meminfo": memory, "pool_bytes": 66 << 30, "guard_bytes": 8 << 30}
            if memory["MemAvailable"] < (74 << 30):
                raise MemoryError("host cannot admit 66 GiB upstream pool plus 8 GiB guard")
            if len(prepared["ranks"]) != 2 or sum(r["logical_bytes"] for r in prepared["ranks"]) != 70329718112:
                raise ValueError("full run requires both complete frozen Qwen ranks")
            result["format_setup"] = prepared
            result["format_establishment_cache_state"] = prepared["cache_state"]
            command = json.loads(Path(slot["command"]).read_text())
            parked = []
            for rank in range(2):
                events = [json.loads(line) for line in (Path(command["metrics"]) / f"rank{rank}.jsonl").read_text().splitlines()]
                lifecycle = [e for e in events if e["event"] in ("load_begin", "weights_ready", "release_begin", "release_end", "load_failed")]
                if not lifecycle or lifecycle[-1]["event"] != "release_end":
                    raise RuntimeError("target_b latest lifecycle does not prove parked state")
                parked.append(lifecycle[-1])
            result["parked_before"] = parked
            engine_spec = slot.get("process_spec")
            if not engine_spec:
                candidates = [path for node_name, path in state.get("basic_owned_processes", [])
                              if node_name == a.node and Path(path).name == "fresh_engine_node29.process.json"]
                if len(candidates) != 1:
                    raise ValueError("target process provenance is not unique")
                engine_spec = candidates[0]
            actual_engine = json.loads(host(["docker", "exec", state["nodes"][a.node]["container"],
                "python", str(script_dir / "engine_probe.py"), "--spec", str(engine_spec)]))
            result["actual_engine_timeout_admission"] = actual_engine
            if actual_engine["handoff_timeout_s"] < a.timeout:
                raise ValueError("actual target handoff timeout is shorter than the Store coordinator deadline; recover a target with sufficient timeout before loading")
            for source, wid in zip(model["sources"], slot["workers"]):
                if workers[wid]["headroom_bytes"]["hbm"] < source["n"] + (512 << 20):
                    raise MemoryError("full target physical HBM admission failed")
        image = host(["docker", "inspect", state["nodes"][a.node]["container"], "--format", "{{.Image}}"])
        result["image_id"] = image
        docker = ["docker", "run", "-d", "--name", name, "--label", "fluidckpt.serverless_run=" + a.run,
                  "--network", "host", "--ipc", "host", "--cpus", "4", "--memory", "96g" if a.mode == "full" else "8g",
                  "--ulimit", "memlock=-1:-1", "--ulimit", "core=0:0", "--security-opt", "seccomp=unconfined"]
        for device in ("/dev/kfd", "/dev/mkfd", "/dev/dri"):
            if host(["test", "-e", device], check=False) == "" and result["commands"][-1]["returncode"] == 0:
                docker += ["--device", device]
        docker += ["-v", f"{ROOT}:{ROOT}", "-v", "/opt/hyhal:/opt/hyhal:ro",
                   "-v", "/data/node28/shared/zhujianian:/data/node28/shared/zhujianian:ro", image, "sleep", "infinity"]
        host(docker)
        created = True
        env = ["env", "PYTHONPATH=" + str(ROOT / "main_lib"), "HSA_FORCE_FINE_GRAIN_PCIE=1",
               "HIP_VISIBLE_DEVICES=0,1", "LD_LIBRARY_PATH=/opt/dtk/dcc/gcvm/lib:/opt/dtk/llvm/lib:/opt/dtk/lib:/opt/dtk/hip/lib:/opt/hyhal/lib:/opt/hyhal/lib64:/opt/dtk/.hyhal/lib:/opt/dtk/.hyhal/lib64:/opt/dtk/.hyhal/rocm_smi/lib"]
        if a.mode == "full":
            probe = json.loads(host(["docker", "exec", name, "python", str(script_dir / "resource_probe.py")]))
            result["cgroup_admission"] = probe
            cap = int(host(["docker", "inspect", name, "--format", "{{.HostConfig.Memory}}"] ))
            result["actual_cgroup_cap_bytes"] = cap
            used = probe["cgroup"].get("/sys/fs/cgroup/memory.current", probe["cgroup"].get("/sys/fs/cgroup/memory/memory.usage_in_bytes"))
            if cap <= 0 or used is None or cap-int(used) < (74 << 30):
                raise MemoryError("adapter cgroup cannot admit the 66 GiB pool plus 8 GiB guard")
        entry = "offset_gate.py" if a.mode == "gate" else "load_engine_storages.py"
        worker_out = out / "worker"
        command = ["docker", "exec", name, *env, "python", str(script_dir / entry),
                   "--source-checkout", str(ROOT / "lib_cite/ServerlessLLM-hip"), "--out", str(worker_out)]
        phase = a.run + "_restore"
        metrics = out / "metrics" / phase
        trigger = out / "verification_start.json"
        if a.mode == "full":
            original_command = Path(slot["command"]).read_bytes()
            command += ["--prepared-data", str(a.prepared_data.resolve()), "--target-handoff", str(Path(state["handoff"]) / phase),
                        "--export-newer-than", str(time.time()), "--verification-trigger", str(trigger), "--timeout", str(a.timeout)]
        argv = host_argv(command)
        result["worker_argv"] = argv
        with (out / "loader.log").open("w") as log:
            process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, preexec_fn=no_core)
        if a.mode == "full":
            wait_path(worker_out / "pool_ready.json", time.monotonic()+a.timeout)
            result["pool_setup"] = json.loads((worker_out / "pool_ready.json").read_text())
            atomic_json(Path(slot["command"]), {"tag": phase, "mode": "restore", "metrics": str(metrics)})
            result["request_begin_monotonic_s"] = time.monotonic()
            resume = Future()
            active = True
            def resume_call():
                try:
                    resume.set_result(http(slot["endpoint"], "resume_memory_occupation", {"tags": ["weights"]}, timeout=a.timeout))
                except BaseException as error:
                    resume.set_exception(error)
            threading.Thread(target=resume_call, daemon=True).start()
            result["resume_ack"] = resume.result(timeout=a.timeout)
            result["timing"] = summarize(metrics, 2)
            result["primary_T_ready_s"] = result["timing"]["load_s"]
            reply = generate(state, slot)
            result["reply"] = reply
            result["text_correct"] = reply["text"] == model["reference"]["text"]
            atomic_json(trigger, {"generation_complete": True})
            if not result["text_correct"]:
                raise RuntimeError("generation differs from frozen exact reference")
            result["request_to_first_correct_token_s"] = reply["first_token_time"]-result["request_begin_monotonic_s"]
        exit_code = process.wait(timeout=a.timeout)
        result["worker_exit_code"] = exit_code
        if exit_code:
            raise RuntimeError("actual upstream worker failed; inspect loader.log")
        result["worker_result"] = json.loads((worker_out / "result.json").read_text())
        if a.mode == "full":
            load_log = (out / "loader.log").read_text(errors="replace")
            result["upstream_odirect_fallback_logged"] = "falling back to compatible mode" in load_log
            result["odirect_observation_scope"] = "upstream opens O_DIRECT first; fallback observed only if explicitly logged; runtime OS/NFS cache unmeasured"
        if result["worker_result"]["status"] != "passed":
            raise RuntimeError("worker correctness gate did not pass")
        if a.mode == "full":
            result["release_ack"] = http(slot["endpoint"], "release_memory_occupation", {"tags": ["weights"]}, timeout=300)
            active = False
            result["release_metrics"] = [json.loads(line) for rank in range(2)
                for line in (metrics / f"rank{rank}.jsonl").read_text().splitlines() if json.loads(line)["event"] == "release_end"]
        result["status"] = "passed"
    except BaseException as error:
        result["status"] = "failed"
        result["error"] = repr(error)
        raise
    finally:
        if process is not None and process.poll() is None:
            result["cleanup"]["pending_worker"] = True
        if active:
            # An unresolved resume owns live allocation state; preserve it for parent recovery.
            if resume is not None and resume.done() and resume.exception() is None:
                try:
                    # A confirmed resume permits stopping the adapter, but the engine remains
                    # allocated until its last importer is gone, including on verification failure.
                    if created:
                        host(["docker", "rm", "-f", name])
                        created = False
                        result["cleanup"]["container_removed_before_release"] = True
                    if process is not None:
                        process.wait(timeout=10)
                        result["cleanup"]["worker_closed_before_release"] = True
                    result["cleanup"]["release_ack"] = http(slot["endpoint"], "release_memory_occupation", {"tags": ["weights"]}, timeout=300)
                    active = False
                except Exception as error:
                    result["cleanup"]["release_error"] = repr(error)
            else:
                result["cleanup"]["pending_resume_preserved"] = True
        if created and not active:
            try:
                host(["docker", "rm", "-f", name])
                result["cleanup"]["container_removed"] = True
                if process is not None and process.poll() is None:
                    process.wait(timeout=10)
                if process is not None:
                    result["cleanup"]["worker_final_exit_code"] = process.returncode
            except Exception as error:
                result["cleanup"]["container_remove_error"] = repr(error)
                result["status"] = "failed"
        if original_command is not None and not active:
            try:
                Path(slot["command"]).write_bytes(original_command)
                result["cleanup"]["original_command_restored"] = True
            except Exception as error:
                result["cleanup"]["command_restore_error"] = repr(error)
                result["status"] = "failed"
        try:
            after_workers, after_replicas = retained_inventory(state)
            result["inventory_after"] = after_workers
            result["protected_metadata_unchanged"] = source_signature(after_replicas) == result.get("protected_before")
            if not active:
                checked_workers = slot["workers"] if a.mode == "full" else [w["worker"] for w in after_workers if w["node"] == a.node]
                released = [after_workers[w]["headroom_bytes"]["hbm"] >= result["inventory_before"][w]["headroom_bytes"]["hbm"]-(256 << 20) for w in checked_workers]
                result["physical_hbm_reclaimed"] = all(released)
                if not all(released) or not result["protected_metadata_unchanged"]:
                    result["status"] = "failed"
        except Exception as error:
            result["cleanup"]["inventory_error"] = repr(error)
            result["status"] = "failed"
        if a.mode == "full" and result.get("code_sha256_before"):
            try:
                result["code_sha256_after"] = freeze_files(ROOT, (a.session, a.prepared_data, a.frozen_source))
                result["code_unchanged"] = result["code_sha256_before"] == result["code_sha256_after"]
                if not result["code_unchanged"]:
                    result["status"] = "failed"
            except Exception as error:
                result["cleanup"]["code_freeze_error"] = repr(error)
                result["status"] = "failed"
        save()
    if result["status"] != "passed":
        raise RuntimeError("post-run cleanup/resource gate failed")
    print(json.dumps({"status": result["status"], "summary": str(out / "summary.json")}))


if __name__ == "__main__":
    main()
