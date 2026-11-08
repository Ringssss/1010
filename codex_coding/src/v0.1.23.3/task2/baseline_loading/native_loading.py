"""Measure the installed vendor SGLang checkpoint loader in an isolated fresh engine.

This entry point doubles as a spawn-safe native server launcher. It never imports
the FluidCKPT capture/restore or lifecycle hooks into its model loading path.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import functools
import hashlib
import inspect
import json
import os
from pathlib import Path
import resource
import shlex
import subprocess
import sys
import time
import traceback

from inspect_baselines import (ROOT, RESULT_ROOT, backend_identity, file_cache, inspect_platform,
                               new_output, parse_json_output)
from runtime_support import (digest, format_contract, generate, host_command, http, load_state,
                             node_command, retained_inventory, source_signature, write)

sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.serving.measure import emit, summarize

SCRIPT = Path(__file__).resolve()
DEFAULT_SESSION = ROOT / "codex_coding/results/v0.1.23.3/task2/basic_scenarios/qwen_bootstrap_r01/session.json"
DEFAULT_REFERENCE = ROOT / "codex_coding/results/v0.1.23.3/task2/basic_scenarios/s2_r01/summary.json"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha_value(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def frozen_reference(state, reference_path, session_path=DEFAULT_SESSION):
    """Use an accepted full-rank source SHA tied to this exact retained snapshot."""
    model = state["models"]["B"]
    run = json.loads(Path(reference_path).read_text())
    if run.get("status") != "passed" or run.get("session_sha256") != digest(session_path):
        raise ValueError("reference must be a passed measurement tied to the retained session")
    ranks = {}
    for case in run["cases"]:
        if case.get("status") != "passed" or not case.get("correct"):
            continue
        verification = case.get("verification") or case.get("flow", {}).get("verification", {})
        if verification.get("passed") is not True:
            continue
        for op in case["flow"]["plan"]["ops"]:
            source = next((s for s in model["sources"] if s["tp"] == op["tp"]), None)
            if (source is None or op["identity"] != source["identity"] or op["replica_id"] not in model["source_ids"]
                    or op["off"] != 0 or op["source_offset"] != 0 or op["n"] != source["n"]):
                continue
            row = verification["operations"][str(op["op_id"])]
            if row["source_sha256"] != row["target_sha256"] or row["n"] != source["n"]:
                raise ValueError("reference SHA is inconsistent")
            key = str(op["tp"])
            previous = ranks.get(key)
            if previous and previous["content_sha256"] != row["source_sha256"]:
                raise ValueError("reference source changed across accepted cases")
            layout = json.loads((Path(state["handoff"]) / "capture" / f"layout.rank{op['tp']}.json").read_text())
            ranks[key] = {"tp": op["tp"], "identity": source["identity"], "bytes": source["n"],
                          "content_sha256": row["source_sha256"], "layout": layout, "layout_sha256": sha_value(layout),
                          "source_id": op["replica_id"], "reference_phase": case["phase"]}
    if set(ranks) != {"0", "1"}:
        raise ValueError("no exact complete TP2 source SHA in accepted reference")
    return {"ranks": ranks, "reference_summary": str(Path(reference_path).resolve()), "reference_summary_sha256": digest(reference_path),
            "scope": "frozen accepted all-byte retained source SHA; current source catalog identity/epoch/generation rechecked"}


def model_fingerprint(model, device, expected):
    """Hash every unique processed storage byte in the captured snapshot order."""
    import torch
    from fluidckpt.serving.layout import scalar_attributes
    from fluidckpt.serving.sglang_hook import _derived_attrs
    seen, storages, tensors, buffers = {}, [], [], {}
    for kind, walk in (("param", model.named_parameters(remove_duplicate=False)),
                       ("buffer", model.named_buffers(remove_duplicate=False)),
                       ("attr", _derived_attrs(model, "model."))):
        for name, tensor in walk:
            if tensor is None or tensor.device.type != "cuda" or tensor.numel() == 0:
                continue
            storage = tensor.untyped_storage()
            key = storage.data_ptr()
            tensors.append({"name": name, "kind": kind, "dtype": str(tensor.dtype), "shape": list(tensor.shape),
                            "stride": list(tensor.stride()), "storage_offset": tensor.storage_offset(),
                            "attributes": scalar_attributes(tensor), "storage_name": seen.get(key, name)})
            if key not in seen:
                seen[key] = name
                storages.append({"name": name, "kind": kind, "nbytes": storage.nbytes(),
                                 "dtype": str(tensor.dtype), "shape": list(tensor.shape)})
                buffers[name] = torch.empty(0, dtype=torch.uint8, device=device).set_(storage, 0, (storage.nbytes(),), (1,))
    layout = {"storages": storages, "tensors": tensors}
    layout_passed = layout == expected["layout"]
    full = hashlib.sha256()
    checked = 0
    per_storage = []
    # Hash the actual native order. A differing layout is rejected independently.
    for row in storages:
        content = hashlib.sha256()
        buffer = buffers[row["name"]]
        for offset in range(0, row["nbytes"], 16 << 20):
            raw = buffer[offset:offset + (16 << 20)].cpu().numpy().tobytes()
            full.update(raw)
            content.update(raw)
            checked += len(raw)
        per_storage.append({"name": row["name"], "bytes": row["nbytes"], "sha256": content.hexdigest()})
    return {"passed": layout_passed and checked == expected["bytes"] and full.hexdigest() == expected["content_sha256"],
            "checked_bytes": checked, "expected_bytes": expected["bytes"], "content_sha256": full.hexdigest(),
            "expected_content_sha256": expected["content_sha256"], "layout_passed": layout_passed,
            "layout_sha256": sha_value(layout), "expected_layout_sha256": expected["layout_sha256"],
            "layout": layout, "storages": per_storage, "algorithm": "SHA256 of every native processed unique storage byte"}


def install_native_measurement():
    if not os.environ.get("NATIVE_LOADING_EXPECTED"):
        return
    import torch
    from sglang.srt.model_executor.model_runner import ModelRunner
    from sglang.srt.model_loader import loader as L
    if getattr(ModelRunner.load_model, "_native_loading_measured", False):
        return
    backend = backend_identity()
    original_runner = ModelRunner.load_model
    original_loader = L.DefaultModelLoader.load_model
    original_init = L._initialize_model

    def source(fn):
        path = inspect.getsourcefile(fn)
        return {"callable": fn.__qualname__, "source_path": path, "source_file_sha256": digest(path) if path else None,
                "source_first_line": fn.__code__.co_firstlineno}

    @functools.wraps(original_loader)
    def native_loader(self, *args, **kwargs):
        rank = self.load_config.tp_rank
        emit(rank, "native_loader_begin", **source(original_loader), load_format=str(self.load_config.load_format))
        try:
            model = original_loader(self, *args, **kwargs)
        except BaseException as exc:
            emit(rank, "native_loader_failed", error=repr(exc))
            raise
        emit(rank, "native_loader_end")
        return model

    @functools.wraps(original_init)
    def initialize_model(*args, **kwargs):
        model = original_init(*args, **kwargs)
        original_weights = model.load_weights
        @functools.wraps(original_weights)
        def load_weights(*a, **kw):
            rank = int(os.environ.get("NATIVE_CURRENT_RANK", "-1"))
            emit(rank, "native_model_load_weights_begin", **source(original_weights))
            result = original_weights(*a, **kw)
            emit(rank, "native_model_load_weights_end")
            return result
        model.load_weights = load_weights
        return model

    if hasattr(L.DefaultModelLoader, "_get_all_weights"):
        original_iterator = L.DefaultModelLoader._get_all_weights
        @functools.wraps(original_iterator)
        def iter_weights(self, *args, **kwargs):
            rank = self.load_config.tp_rank
            emit(rank, "native_weight_iterator_begin", **source(original_iterator))
            count = 0
            try:
                for value in original_iterator(self, *args, **kwargs):
                    count += 1
                    yield value
            finally:
                emit(rank, "native_weight_iterator_end", checkpoint_tensors=count)
        L.DefaultModelLoader._get_all_weights = iter_weights

    if hasattr(L.DefaultModelLoader, "_prepare_weights"):
        original_prepare = L.DefaultModelLoader._prepare_weights
        @functools.wraps(original_prepare)
        def prepare_weights(self, *args, **kwargs):
            rank = self.load_config.tp_rank
            emit(rank, "native_prepare_weights_begin", **source(original_prepare))
            value = original_prepare(self, *args, **kwargs)
            emit(rank, "native_prepare_weights_end", prepared_checkpoint=str(value))
            return value
        L.DefaultModelLoader._prepare_weights = prepare_weights

    @functools.wraps(original_runner)
    def measured(self, *args, **kwargs):
        rank = self.tp_rank
        os.environ["NATIVE_CURRENT_RANK"] = str(rank)
        emit(rank, "native_backend", backend=backend, runner=source(original_runner))
        cache = file_cache(Path(os.environ["NATIVE_MODEL_PATH"]))
        write(Path(os.environ["NATIVE_RUN_OUT"]) / f"cache_rank{rank}_before.json", cache)
        if os.environ.get("NATIVE_CACHE_STATE") == "warm_file_cache" and cache["resident_fraction"] < .99:
            raise RuntimeError("warm sampled file-page residency fell below 99% before native load")
        emit(rank, "load_begin", native_fresh_engine=True)
        try:
            result = original_runner(self, *args, **kwargs)
            torch.cuda.synchronize()
        except BaseException as exc:
            emit(rank, "load_failed", error=repr(exc))
            raise
        emit(rank, "weights_ready", allocated_bytes=torch.cuda.memory_allocated())
        # Both ranks finish loading before any all-byte hashing competes for CPU,
        # PCIe, or host memory bandwidth. The barrier is outside each ready event.
        from sglang.srt.distributed import get_tp_group
        torch.distributed.barrier(group=get_tp_group().cpu_group)
        stamp = time.monotonic()
        expected = json.loads(Path(os.environ["NATIVE_LOADING_EXPECTED"]).read_text())["ranks"][str(rank)]
        fingerprint = model_fingerprint(self.model, f"cuda:{self.gpu_id}", expected)
        fingerprint["post_ready_verification_s"] = time.monotonic() - stamp
        write(Path(os.environ["NATIVE_RUN_OUT"]) / f"fingerprint_rank{rank}.json", fingerprint)
        emit(rank, "native_fingerprint_complete", passed=fingerprint["passed"], checked_bytes=fingerprint["checked_bytes"])
        write(Path(os.environ["NATIVE_RUN_OUT"]) / f"cache_rank{rank}_after.json", file_cache(Path(os.environ["NATIVE_MODEL_PATH"])))
        if not fingerprint["passed"]:
            raise AssertionError(f"rank {rank} native processed content/layout differs from frozen reference")
        return result

    measured._native_loading_measured = True
    L.DefaultModelLoader.load_model = native_loader
    L._initialize_model = initialize_model
    ModelRunner.load_model = measured


install_native_measurement()


def argument(command, name, value):
    command = list(command)
    if name in command:
        command[command.index(name) + 1] = str(value)
    else:
        command += [name, str(value)]
    return command


def prepare_spec(state, out, args):
    template_path = Path(state["basic_ownership_run"]) / f"fresh_engine_{args.node}.process.json"
    template = json.loads(template_path.read_text())
    command = template["command"].copy()
    command[1] = str(SCRIPT)
    command.insert(2, "--engine")
    for key, value in (("--port", args.port), ("--nccl-port", args.nccl_port), ("--load-format", "auto"), ("--model-path", args.model_path)):
        command = argument(command, key, value)
    env = {key: value for key, value in template["env"].items() if not key.startswith("FLUIDCKPT_")}
    env.update(FLUIDCKPT_METRICS=str(out / "metrics/native"), NATIVE_LOADING_EXPECTED=str(out / "expected.json"),
               NATIVE_MODEL_PATH=str(args.model_path), NATIVE_RUN_OUT=str(out), NATIVE_CACHE_STATE=args.cache_state)
    return {"command": command, "env": env, "cwd": str(ROOT), "log": str(out / "native_engine.log"),
            "template_path": str(template_path), "template_sha256": digest(template_path)}


def check_admission(preflight, model, args):
    fields = {line.split(":")[0]: int(line.split()[1]) << 10 for line in preflight["probes"]["host_memory"]["stdout"].splitlines()
              if ":" in line and len(line.split()) >= 2 and line.split()[1].isdigit()}
    minimum = args.memory_gib << 30
    if fields["MemAvailable"] < minimum:
        raise MemoryError("host MemAvailable smaller than isolated container cap")
    gpu = parse_json_output(preflight["probes"]["gpu_memory"]["stdout"])
    gpu = gpu.get("gpu_memory", gpu)
    admission = []
    for source in model["sources"]:
        row = gpu[f"card{source['tp']}"]
        free = (int(row["vram Total Memory (MiB)"]) - int(row["vram Total Used Memory (MiB)"])) << 20
        required = source["n"] + (args.guard_gib << 30)
        if free < required:
            raise MemoryError(f"GPU{source['tp']} native admission: {free} < {required}")
        admission.append({"gpu": source["tp"], "free_bytes": free, "required_bytes": required})
    occupied = set()
    for line in preflight["probes"]["tcp"]["stdout"].splitlines():
        parts = line.split()
        if len(parts) > 3 and ":" in parts[1] and parts[3] != "06":
            try:
                occupied.add(int(parts[1].rsplit(":", 1)[1], 16))
            except ValueError:
                pass
    if {args.port, args.nccl_port} & occupied:
        raise ValueError("native server/NCCL port already occupied")
    if args.memory_gib < 96:
        raise MemoryError("native TP2 loader container cap must be at least 96GiB")
    return {"host_available_bytes": fields["MemAvailable"], "container_cap_bytes": minimum,
            "gpu_admission": admission, "ports": [args.port, args.nccl_port]}


def command_in_container(node, container, command, timeout=60):
    script = "ulimit -c 0\nsource /opt/dtk/env.sh >/dev/null 2>&1\nexec " + shlex.join(command)
    result = host_command(node, ["docker", "exec", container, "bash", "-lc", script], timeout=timeout)
    if result.get("returncode") != 0:
        raise RuntimeError(f"owned container command failed: {result}")
    return result


def run(args):
    out = new_output(args.out)
    state = load_state(args.session)
    if state["models"]["B"]["tp"] != 2 or str(args.model_path) != "/data/node28/shared/zhujianian/Qwen3.6-35B-A3B":
        raise ValueError("native protocol is fixed to complete Qwen3.6-35B-A3B TP2")
    result = {"status": "registered", "measurement_status": "not_measured", "run_id": out.name,
              "scenario_id": "S2", "scenario_variant": "native_fresh_engine_file_cache", "development": "v0.1.23.3", "task": "task2",
              "adaptation_identity": "haiguang_vendor_sglang_native_checkpoint", "target_slot": args.node + "_isolated_fresh",
              "cases": [], "cleanup_errors": [], "owned_remaining": [], "session_sha256": digest(args.session),
              "format_contract": format_contract(state["models"]["B"]),
              "protocol": {"primary_metric": "same-host max(weights_ready)-min(load_begin)", "repetitions": 1,
                           "fresh_process_startup_separate": True, "cache_state": args.cache_state,
                           "native_path": "installed DefaultModelLoader(AUTO) implementations preserved under boundary/stage observation wrappers",
                           "not_frozen_upstream_original": True, "verification": "ready then all processed storage bytes/layout SHA plus exact frozen 32-token output",
                           "verification_in_startup": True, "source_sha_scope": "frozen accepted SHA, current source metadata rechecked",
                           "fixture_dram_bytes": sum(s["n"] for s in state["models"]["B"]["sources"]),
                           "native_uses_fixture_dram": False, "file_cache_counted_as_separate_host_residency": True},
              "code_sha256": {str(p.relative_to(ROOT)): digest(p) for p in (SCRIPT, Path(__file__).with_name("inspect_baselines.py"),
                               ROOT / "codex_coding/src/v0.1.23.3/task2/basic_scenarios/runtime_support.py",
                               ROOT / "codex_coding/src/task2/cluster/process.py",
                               ROOT / "main_lib/fluidckpt/serving/measure.py", ROOT / "main_lib/fluidckpt/serving/layout.py", ROOT / "main_lib/fluidckpt/serving/sglang_hook.py")}}
    result["protocol"]["cache_scope"] = "Linux buffered file-page cache, not explicitly pinned host weights; underlying NFS/storage cache not asserted"
    def save():
        write(out / "summary.json", result)
    write(out / "commands.json", {"argv": [sys.executable, *sys.argv], "shell": shlex.join([sys.executable, *sys.argv])})
    save()
    owned = False
    start_attempted = False
    process_stopped = False
    container = "zjn-fluid-native-" + hashlib.sha256(str(out).encode()).hexdigest()[:14]
    try:
        expected = frozen_reference(state, args.reference_summary, args.session)
        write(out / "expected.json", expected)
        spec = prepare_spec(state, out, args)
        spec_path = out / "native_engine.process.json"
        write(spec_path, spec)
        result["prepared_process_spec"] = str(spec_path)
        result["expected_reference"] = {k: v for k, v in expected.items() if k != "ranks"}
        result["isolated_container"] = container
        result["frozen_upstream_commits"] = {r["name"]: r["commit"] for r in json.loads((ROOT / "lib_cite/baselines.lock.json").read_text())["repositories"]}
        if not args.execute:
            save()
            return result
        preflight = inspect_platform(state, args.model_path, args.node, out / "raw_preflight_probes.json")
        write(out / "preflight.json", preflight)
        result["admission"] = check_admission(preflight, state["models"]["B"], args)
        result["backend"] = preflight["backend"]
        before_signature = preflight["base_source_signature"]
        save()
        image = host_command(args.node, ["docker", "inspect", state["nodes"][args.node]["container"], "--format", "{{.Image}}"])
        if image.get("returncode") != 0:
            raise RuntimeError("cannot inspect existing vendor image")
        result["vendor_image_id"] = image["stdout"].strip()
        docker = ["docker", "run", "-d", "--name", container, "--label", "fluidckpt.native_run=" + str(out),
                  "--network", "host", "--ipc", "host", "--cap-add", "SYS_NICE", "--ulimit", "memlock=-1:-1", "--ulimit", "core=0:0",
                  "--memory", f"{args.memory_gib}g", "--memory-swap", f"{args.memory_gib}g"]
        for device in ("/dev/kfd", "/dev/mkfd", "/dev/dri", "/dev/infiniband"):
            docker += ["--device", device]
        for mount in ("/opt/hyhal:/opt/hyhal:ro", "/home/zhujianian:/home/zhujianian", "/data/node28/shared/zhujianian:/data/node28/shared/zhujianian:ro"):
            docker += ["-v", mount]
        docker += [image["stdout"].strip(), "sleep", "infinity"]
        result["container_create"] = host_command(args.node, docker, timeout=120)
        if result["container_create"].get("returncode") != 0:
            raise RuntimeError("isolated container creation failed")
        owned = True
        result["owned_remaining"] = [container]
        save()
        mode = "warm" if args.cache_state == "warm_file_cache" else "cache"
        cache_result = command_in_container(args.node, container, ["python3", str(SCRIPT.with_name("inspect_baselines.py")), mode,
                                                                 "--model-path", str(args.model_path)], timeout=args.timeout)
        cache = parse_json_output(cache_result["stdout"])
        write(out / "cache_preparation.json", cache)
        cache_measurement = cache.get("cache", cache)
        if args.cache_state == "warm_file_cache" and cache_measurement["resident_fraction"] < .99:
            raise RuntimeError("sequential warmup did not achieve >=99% sampled page residency")
        result["cache_preparation"] = {"path": str(out / "cache_preparation.json"), "resident_fraction": cache_measurement["resident_fraction"]}
        phase = {"phase": "native", "method_id": "sglang_vendor_native", "adaptation_identity": result["adaptation_identity"],
                 "status": "running", "cache_state": args.cache_state, "correct": False, "target_slot": result["target_slot"]}
        result["cases"].append(phase)
        result["status"] = "running"
        stamp = time.monotonic()
        phase["process_start_request_monotonic_s"] = stamp
        start_attempted = True
        result["process_start"] = command_in_container(args.node, container, ["python3", str(ROOT / "codex_coding/src/task2/cluster/process.py"), "start", "--spec", str(spec_path)])
        endpoint = f"http://{state['nodes'][args.node]['ip']}:{args.port}"
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            if all((out / f"fingerprint_rank{rank}.json").exists() for rank in range(2)):
                phase["timing"] = summarize(out / "metrics/native", 2)
                phase["primary_T_ready_s"] = phase["timing"]["load_s"]
                fingerprints = [json.loads((out / f"fingerprint_rank{rank}.json").read_text()) for rank in range(2)]
                if not all(f["passed"] for f in fingerprints):
                    raise AssertionError("native full content/layout verification failed")
                try:
                    info = http(endpoint, "get_model_info", timeout=2)
                    break
                except Exception:
                    pass
            log = out / "native_engine.log"
            tail = log.read_text(errors="replace")[-32768:] if log.exists() else ""
            if any(marker in tail for marker in ("Traceback (most recent call last)", "OutOfMemoryError", "VM fault", "Received sigquit")):
                raise RuntimeError("native engine failed; inspect native_engine.log")
            time.sleep(.25)
        else:
            raise TimeoutError("fresh native engine startup/verification deadline")
        phase["fresh_start_to_model_info_s"] = time.monotonic() - stamp
        phase["get_model_info"] = info
        phase["verification"] = {"passed": True, "checked_bytes": sum(f["checked_bytes"] for f in fingerprints),
                                 "algorithm": "every processed storage byte SHA256 and exact layout vs frozen accepted retained reference",
                                 "rank_artifacts": [str(out / f"fingerprint_rank{rank}.json") for rank in range(2)]}
        phase["post_ready_verification_s"] = max(f["post_ready_verification_s"] for f in fingerprints)
        phase["exports"] = [{"rank": rank, "nbytes": f["checked_bytes"], "kind": "fingerprint_only_no_ipc_export"} for rank, f in enumerate(fingerprints)]
        phase["reply"] = generate(state, {"endpoint": endpoint})
        phase["correct"] = phase["reply"]["text"] == state["models"]["B"]["reference"]["text"]
        phase["request_to_first_correct_token_s"] = phase["reply"]["first_token_time"] - stamp if phase["correct"] else None
        if not phase["correct"]:
            raise AssertionError("native deterministic output differs from frozen reference")
        for detail in phase["timing"]["ranks"].values():
            if not {"native_loader_begin", "native_loader_end", "native_model_load_weights_begin", "native_model_load_weights_end"} <= {e["event"] for e in detail["events"]}:
                raise ValueError("missing actual vendor/native loader invocation trace")
            backend_events = [e for e in detail["events"] if e["event"] == "native_backend"]
            if len(backend_events) != 1 or backend_events[0]["backend"] != result["backend"]:
                raise ValueError("installed native backend differs from preflight freeze")
        phase["status"] = "passed"
        result["measurement_status"] = "measured"
        result["status"] = "passed"
        save()
    except BaseException as exc:
        result["status"] = "failed"
        result["error"] = repr(exc)
        write(out / "failure.json", {"error": repr(exc), "traceback": traceback.format_exc()})
        if result["cases"]:
            result["cases"][-1]["status"] = "failed"
        save()
    finally:
        if owned:
            try:
                ownership = host_command(args.node, ["docker", "inspect", container, "--format", '{{index .Config.Labels "fluidckpt.native_run"}}'])
                if ownership.get("returncode") != 0 or ownership["stdout"].strip() != str(out):
                    raise RuntimeError("native container ownership label mismatch; refusing cleanup")
                if start_attempted:
                    stopped = command_in_container(args.node, container, ["python3", str(ROOT / "codex_coding/src/task2/cluster/process.py"), "stop", "--spec", str(out / "native_engine.process.json")], timeout=60)
                    result["process_cleanup"] = stopped
                    process_stopped = True
                removed = host_command(args.node, ["docker", "rm", "-f", container], timeout=60)
                if removed.get("returncode") != 0:
                    raise RuntimeError(f"owned container removal failed: {removed}")
                result["container_cleanup"] = removed
                result["owned_remaining"] = []
            except BaseException as exc:
                result["cleanup_errors"].append(repr(exc))
        if args.execute and "before_signature" in locals():
            try:
                workers, replicas = retained_inventory(state)
                result["retained_base_unchanged"] = source_signature([r for r in replicas if r["id"] in state["models"]["B"]["source_ids"]]) == before_signature
                write(out / "after_inventory.json", {"workers": workers, "replicas": replicas})
                if not result["retained_base_unchanged"]:
                    raise AssertionError("protected retained source catalog changed")
            except BaseException as exc:
                result["cleanup_errors"].append(repr(exc))
        if result["cleanup_errors"]:
            result["status"] = "failed"
        drift = [path for path, expected_sha in result["code_sha256"].items() if digest(ROOT / path) != expected_sha]
        result["source_hash_mismatches"] = drift
        if drift:
            result["status"] = "failed"
        save()
    return result


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if "--engine" in sys.argv:
        from sglang.launch_server import run_server
        from sglang.srt.plugins import load_plugins
        from sglang.srt.server_args import prepare_server_args
        from sglang.srt.utils import kill_process_tree
        load_plugins()
        try:
            run_server(prepare_server_args([a for a in sys.argv[1:] if a != "--engine"]))
        finally:
            kill_process_tree(os.getpid(), include_parent=False)
        return
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--session", type=Path, default=DEFAULT_SESSION)
    p.add_argument("--reference-summary", type=Path, default=DEFAULT_REFERENCE)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--model-path", type=Path, default=Path("/data/node28/shared/zhujianian/Qwen3.6-35B-A3B"))
    p.add_argument("--node", choices=("node27", "node29"), default="node29")
    p.add_argument("--port", type=int, default=33640)
    p.add_argument("--nccl-port", type=int, default=33740)
    p.add_argument("--memory-gib", type=int, default=128)
    p.add_argument("--guard-gib", type=int, default=12)
    p.add_argument("--cache-state", choices=("warm_file_cache", "observed_file_cache"), default="warm_file_cache")
    p.add_argument("--timeout", type=float, default=1800)
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()
    result = run(args)
    print(json.dumps({"status": result["status"], "out": str(args.out), "measurement_status": result["measurement_status"]}))
    if result["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
