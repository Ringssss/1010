"""CPU-safe helpers for the basic scenario measurement drivers."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[5]
RESULT_ROOT = ROOT / "codex_coding/results/v0.1.23.3/task2/basic_scenarios"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def new_run(path):
    path = Path(path).resolve()
    if RESULT_ROOT.resolve() not in path.parents:
        raise ValueError("output must be below the versioned basic_scenarios result root")
    path.mkdir(parents=True, exist_ok=False)
    return path


def load_state(path):
    state = json.loads(Path(path).read_text())
    root_path = Path(state["root_session"])
    root = json.loads(root_path.read_text())
    for field in ("endpoints", "nodes", "members", "handoff"):
        state.setdefault(field, root.get(field, str(root_path.parent / "handoff")))
    return state


def http(endpoint, operation, body=None, timeout=15):
    if operation.split("?")[0] == "health":
        raise ValueError("health can execute a forward on parked meta weights")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    payload = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(endpoint + "/" + operation, data=payload,
                                     headers={"Content-Type": "application/json"})
    with opener.open(request, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


def retained_inventory(state):
    with ThreadPoolExecutor(len(state["endpoints"])) as pool:
        workers = list(pool.map(lambda ep: http(ep, "inventory?compact=1"), state["endpoints"]))
    if [w["worker"] for w in workers] != list(range(len(workers))):
        raise ValueError("worker order differs from session endpoints")
    if len({w["session"] for w in workers}) != 1 or any(w["transactions"] for w in workers):
        raise ValueError("mixed keeper sessions or active transactions")
    return workers, [r for w in workers for r in w["replicas"] if r["state"] == "VALID"]


def select_base(model, replicas):
    wanted = set(model["source_ids"])
    rows = [r for r in replicas if r["id"] in wanted]
    if len(rows) != len(wanted) or len(wanted) != len(model["source_ids"]):
        raise ValueError("missing or duplicate retained source IDs")
    if any(r["tier"] != "dram" or r["state"] != "VALID" for r in rows):
        raise ValueError("S2/S3 require the exact full retained DRAM base")
    for source in model["sources"]:
        rank_rows = sorted((r["off"], r["off"] + r["n"]) for r in rows
                           if r["identity"] == source["identity"] and r["tp"] == source["tp"])
        cursor = 0
        for begin, end in rank_rows:
            if begin > cursor or begin < 0 or end > source["n"]:
                raise ValueError("invalid retained rank extent")
            cursor = max(cursor, end)
        if cursor != source["n"]:
            raise ValueError("retained DRAM rank coverage incomplete")
    return rows


def target(source, rid, worker, **extra):
    row = {"key": rid, "id": rid, "mode": "replica", "worker": worker,
           "tier": "hbm", "identity": source["identity"], "tp": source["tp"],
           "off": 0, "n": source["n"], "meta": source["meta"], "index": source["index"]}
    row.update(extra)
    return row


def format_contract(model):
    tensors = [t for source in model["sources"] for t in source["meta"].get("tensors", [])]
    return {"model_version": str(model["sources"][0]["meta"].get("model_identity", "captured")),
            "dtype": sorted({t["dtype"] for t in tensors}), "quantization": "captured_processed_layout",
            "tp": model["tp"], "processed_snapshot_id": [s["identity"] for s in model["sources"]]}


def source_signature(rows):
    fields = ("id", "worker", "identity", "tp", "off", "n", "tier", "epoch", "generation", "state")
    return sorted(tuple(r.get(k) for k in fields) for r in rows)


def generate(state, slot):
    payload = {"text": state.get("prompt", "The capital of France is"),
               "sampling_params": {"temperature": 0, "max_new_tokens": 32}, "stream": True}
    request = urllib.request.Request(slot["endpoint"] + "/generate", data=json.dumps(payload).encode(),
                                    headers={"Content-Type": "application/json"})
    first, last = None, {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=180) as response:
        for raw in response:
            if not raw.startswith(b"data: "):
                continue
            payload = raw[6:].strip()
            if payload == b"[DONE]":
                break
            last = json.loads(payload)
            if first is None and (last.get("text") or last.get("meta_info", {}).get("completion_tokens", 0)):
                first = time.monotonic()
    if first is None:
        raise RuntimeError("no generated token")
    return {"text": last["text"], "first_token_time": first,
            "tokens": last.get("meta_info", {}).get("completion_tokens")}


def node_command(node, state, command, timeout=20):
    argv = ["docker", "exec", state["nodes"][node]["container"], *command]
    if node != "node27":
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", node, shlex.join(argv)]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return {"command": argv, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": argv, "returncode": None, "error": repr(exc)}


def host_command(node, command, timeout=20):
    argv = command
    if node != "node27":
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", node, shlex.join(command)]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return {"command": argv, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": argv, "returncode": None, "error": repr(exc)}


def code_freeze():
    files = list((ROOT / "main_lib/fluidckpt").rglob("*.py"))
    files += list(Path(__file__).parent.glob("*.py"))
    files += [ROOT / "codex_coding/src/v0.1.23.3/task2/paper_e1_e8/runtime/restore_engine.py",
              ROOT / "codex_coding/src/v0.1.23.3/task2/paper_e1_e8/e1/run_ipc_gate.py",
              ROOT / "codex_coding/src/task2/cluster/run_system.py", ROOT / "codex_coding/src/task2/cluster/process.py",
              ROOT / "codex_coding/src/task2/cluster/inspect_node.py", ROOT / "codex_coding/src/serving/sglang_fluid_launch.py"]
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(files)}


def bootstrap(args):
    """Reuse the existing SystemRun process/keeper driver in a new four-worker namespace."""
    import argparse
    import os
    import sys
    sys.path.insert(0, str(ROOT / "main_lib"))
    sys.path.insert(0, str(ROOT / "codex_coding/src/task2/cluster"))
    from run_system import NODES, SystemRun
    from fluidckpt.cluster.control import request
    from fluidckpt.memory.bindings import choose_bindings
    from fluidckpt.serving.handoff import wait_for_exports
    from fluidckpt.serving.measure import summarize

    reference = load_state(args.reference_session)
    root_path = Path(reference["root_session"])
    template_path = root_path.parent / "model_B_node27_reclaim02.process.json"
    template = json.loads(template_path.read_text())
    out = Path(args.out).resolve()
    if RESULT_ROOT.resolve() not in out.parents:
        raise ValueError("bootstrap output must be below basic_scenarios result root")
    name = "zjn-fluid-basic-" + hashlib.sha256(str(out).encode()).hexdigest()[:14]
    nodes = {node: dict(reference["nodes"][node], gpus=[0, 1], container=name) for node in ("node27", "node29")}
    NODES.clear()
    NODES.update(nodes)
    driver_args = argparse.Namespace(run=out.name, result_root=out.parent, port_base=args.port_base,
        development="v0.1.23.3", model=str(args.model_path), worker_serialize=0,
        mesh="node27:0-1,node29:0-1", handoff_poll=.05)
    run = SystemRun(driver_args)
    run.env = template["env"].copy()
    run.env.pop("FLUIDCKPT_CPU_OWNER_SOCKET", None)
    for key in list(run.env):
        if key.startswith("FLUIDCKPT_"):
            run.env.pop(key)
    run.env["PYTHONPATH"] = str(ROOT / "main_lib")
    run.env["AMD_SERIALIZE_KERNEL"] = "0"
    run.result.update(status="registered", scope="Fresh Qwen-only four-worker bootstrap; no baseline performance sample",
                      template_sha256=digest(template_path), template_path=str(template_path),
                      native_load_reference="generate before DRAM capture; native engine remains initialized",
                      memory_limits_gib={"node27": args.source_memory_gib, "node29": args.target_memory_gib},
                      owned_namespace=name, cleanup_errors=[], code_sha256=code_freeze(),
                      experiment_id="e1", gate_kind="capability_gate")
    run.save()
    if not args.execute:
        return {"status": "registered_only", "out": str(out), "measurement_status": "not_measured"}

    def set_arg(command, key, value):
        if key in command:
            command[command.index(key)+1] = str(value)
        else:
            command.extend([key, str(value)])

    def launch_engine(node, phase, port, mode, nccl_port):
        command = template["command"].copy()
        set_arg(command, "--model-path", args.model_path)
        set_arg(command, "--port", port)
        set_arg(command, "--nccl-port", nccl_port)
        set_arg(command, "--load-format", "auto" if mode == "capture" else "dummy")
        command_path = out / f"{node}_engine.command.json"
        metrics = out / "metrics" / phase
        write(command_path, {"tag": phase, "mode": "restore", "metrics": str(metrics)})
        env = dict(template["env"], HIP_VISIBLE_DEVICES="0,1", FLUIDCKPT_MODE=mode,
            FLUIDCKPT_TAG=phase, FLUIDCKPT_HANDOFF=str(out / "handoff"), FLUIDCKPT_METRICS=str(metrics),
            FLUIDCKPT_CAPTURE_WAIT="0", FLUIDCKPT_TIMEOUT="120", FLUIDCKPT_MODEL_ID=model_identity,
            FLUIDCKPT_TP_SIZE="2", FLUIDCKPT_MANIFEST=str(out / "handoff/capture/derived.json"),
            FLUIDCKPT_LIFECYCLE=str(command_path))
        env.pop("FLUIDCKPT_CPU_OWNER_SOCKET", None)
        handle = run.start(node, f"fresh_engine_{node}", command, env)
        slot = {"model": "B", "endpoint": f"http://{nodes[node]['ip']}:{port}",
                "workers": [0, 1] if node == "node27" else [2, 3], "command": str(command_path),
                "parked": False, "ownership_run": str(out), "metrics": str(metrics),
                "handoff": str(out / "handoff"), "manifest": env["FLUIDCKPT_MANIFEST"]}
        return slot, handle, metrics

    def cleanup_failure():
        # Stop importing model engines before their keepers; all handles are new.
        ordered = sorted(list(run.processes), key=lambda h: ("fresh_engine" not in h[1].name, str(h[1])))
        for handle in ordered:
            try:
                run.stop(handle)
            except Exception as exc:
                run.result["cleanup_errors"].append(repr(exc))
        if not run.processes:
            for node, container in list(run.owned_containers):
                try:
                    run.docker_host(node, ["rm", "-f", container])
                    run.owned_containers.remove((node, container))
                except Exception as exc:
                    run.result["cleanup_errors"].append(repr(exc))
        run.result["remaining_owned_processes"] = [(n, str(p)) for n, p in run.processes]
        run.result["remaining_owned_containers"] = run.owned_containers

    def wait_exports(phase):
        paths = [out / "handoff" / phase / f"rank{rank}.pkl" for rank in range(2)]
        deadline = time.monotonic()+args.timeout
        while not all(path.exists() for path in paths):
            for node, spec_path in run.processes:
                if "fresh_engine" not in spec_path.name:
                    continue
                log = out / spec_path.name.replace(".process.json", ".log")
                tail = log.read_text(errors="replace")[-32768:] if log.exists() else ""
                if any(marker in tail for marker in ("Traceback (most recent call last)", "OutOfMemoryError", "Received sigquit", "VM fault")):
                    raise RuntimeError(f"fresh engine failed before exports; inspect {log}")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"bootstrap exports deadline: {phase}")
            time.sleep(.1)

    try:
        if not args.model_path.is_dir():
            raise FileNotFoundError(args.model_path)
        stamp = time.monotonic()
        for node in nodes:
            memory = host_command(node, ["cat", "/proc/meminfo"])
            if memory.get("returncode") != 0:
                raise RuntimeError(f"host memory probe failed: {node}")
            fields = {line.split(":")[0]: int(line.split()[1]) << 10 for line in memory["stdout"].splitlines() if ":" in line and line.split()[1].isdigit()}
            minimum = (96 if node == "node27" else 16) << 30
            run.result.setdefault("host_admission", {})[node] = dict(memory, minimum_available_bytes=minimum)
            if fields["MemAvailable"] < minimum:
                raise MemoryError(f"host DRAM admission failed: {node}")
            previous_name = reference["nodes"][node]["container"]
            image = run.docker_host(node, ["inspect", previous_name, "--format", "{{.Config.Image}}"]).strip()
            limit = args.source_memory_gib if node == "node27" else args.target_memory_gib
            argv = ["run", "-d", "--name", name, "--label", "fluidckpt.basic_run="+str(out),
                    "--network", "host", "--ipc", "host", "--cap-add", "SYS_NICE",
                    "--ulimit", "memlock=-1:-1", "--ulimit", "core=0:0",
                    "--memory", f"{limit}g", "--memory-swap", f"{limit}g"]
            for device in ("/dev/kfd", "/dev/mkfd", "/dev/dri", "/dev/infiniband"):
                argv.extend(["--device", device])
            for mount in ("/opt/hyhal:/opt/hyhal:ro", "/home/zhujianian:/home/zhujianian",
                          "/data/node28/shared/zhujianian:/data/node28/shared/zhujianian:ro"):
                argv.extend(["-v", mount])
            run.docker_host(node, argv + [image, "sleep", "infinity"])
            run.owned_containers.append((node, name))
            run.result.setdefault("container_commands", {})[node] = argv + [image, "sleep", "infinity"]
            run.result.setdefault("container_effective_limits", {})[node] = run.docker_host(node,
                ["inspect", name, "--format", "{{json .HostConfig.Memory}} {{json .HostConfig.MemorySwap}} {{json .State}}"])
            run.result.setdefault("container_cgroup_memory", {})[node] = run.remote(node,
                ["bash", "-c", "if test -f /sys/fs/cgroup/memory.max; then cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.events; else cat /sys/fs/cgroup/memory/memory.limit_in_bytes /sys/fs/cgroup/memory/memory.usage_in_bytes /sys/fs/cgroup/memory/memory.failcnt; fi"])
        probes = {}
        for node in nodes:
            probe_path = out / f"{node}_preflight.json"
            run.remote(node, ["python3", "codex_coding/src/task2/cluster/inspect_node.py", "--out", str(probe_path)])
            probe = json.loads(probe_path.read_text())
            occupied = set(probe.get("occupied_tcp_ports", probe["listening_ports"]))
            requested_ports = {args.port_base, args.port_base+1, args.port_base+100,
                               args.source_port, args.target_port, args.nccl_port, args.nccl_port+1}
            if requested_ports & occupied:
                raise RuntimeError(f"fresh namespace port occupied: {node}")
            for gpu in (0, 1):
                gpu_memory = probe["gpu_memory"][f"card{gpu}"]
                free = (int(gpu_memory["vram Total Memory (MiB)"])-int(gpu_memory["vram Total Used Memory (MiB)"])) << 20
                if free < 48 << 30:
                    raise MemoryError(f"Qwen runtime HBM guard failed: {node}:GPU{gpu}")
            probes[node] = probe
        run.probes = probes
        # Allocate one full host rank close to each source GPU; no remote DRAM clone.
        bindings = choose_bindings(probes["node27"]["topology"], [{"gpu": g, "nbytes": 40 << 30} for g in (0, 1)])
        driver_args.worker_numas = {f"node27:{r['gpu']}": r["numa"] for r in bindings["bindings"]}
        target_bindings = choose_bindings(probes["node29"]["topology"], [{"gpu": g, "nbytes": 4 << 30} for g in (0, 1)])
        driver_args.worker_numas.update({f"node29:{r['gpu']}": r["numa"] for r in target_bindings["bindings"]})
        run.result["numa_admission"] = {"node27": bindings, "node29": target_bindings}
        run.result["preflight"] = probes
        run.start_workers()
        run.result["worker_setup_s"] = time.monotonic()-stamp
        model_digest = hashlib.sha256()
        for filename in ("config.json", "model.safetensors.index.json"):
            model_digest.update((args.model_path / filename).read_bytes())
        for path in sorted(args.model_path.glob("*.safetensors")):
            model_digest.update(f"{path.name}:{path.stat().st_size}:{path.stat().st_mtime_ns}".encode())
        model_identity = model_digest.hexdigest()
        started = time.monotonic()
        source_slot, source_handle, native_metrics = launch_engine("node27", "capture", args.source_port, "capture", args.nccl_port)
        wait_exports("capture")
        run.wait_http(source_slot["endpoint"], "get_model_info", timeout=args.timeout)
        new_reference = generate({"prompt": reference.get("prompt", "The capital of France is")}, source_slot)
        run.result["native_load_and_reference_s"] = time.monotonic()-started
        run.result["native_load_timing"] = summarize(native_metrics, 2)
        run.result["native_reference"] = new_reference
        run.result["old_reference_matches"] = new_reference["text"] == reference["models"]["B"]["reference"]["text"]
        run.save()
        started = time.monotonic()
        with ThreadPoolExecutor(2) as pool:
            captures = list(pool.map(lambda rank: request(run.endpoints[rank], "capture", {"tag": "capture", "snap": "basic_B"}, timeout=args.timeout), range(2)))
            checks = list(pool.map(lambda rank: request(run.endpoints[rank], "verify_capture", {"tag": "capture", "snap": "basic_B", "tp": rank}, timeout=args.timeout), range(2)))
        if not all(row["passed"] for row in checks):
            raise AssertionError("native DRAM capture full-byte verification failed")
        sources = run.adopt("basic_B", out.name+"/base")
        for rank in range(2):
            request(run.endpoints[rank], "release_live", {"tag": "capture"})
        run.result["capture_dram_s"] = time.monotonic()-started
        run.result["capture_dram"] = {"captures": captures, "verification": checks}
        model = {"tp": 2, "sources": sources, "source_ids": [s["id"] for s in sources],
                 "reference": new_reference, "identity": model_identity}
        http(source_slot["endpoint"], "release_memory_occupation", {"tags": ["weights"]}, timeout=300)
        source_slot["parked"] = True
        started = time.monotonic()
        target_slot, target_handle, target_metrics = launch_engine("node29", "target_bootstrap", args.target_port, "restore", args.nccl_port+1)
        wait_exports("target_bootstrap")
        targets = [target(s, f"bootstrap/rank{rank}", rank+2, mode="live", tag="target_bootstrap", metrics_directory=str(target_metrics)) for rank, s in enumerate(sources)]
        flow = run.cluster.transfer(run.cluster.plan_restore(targets, "resource_lp", model["source_ids"]), verify_before_ready=True)
        run.wait_http(target_slot["endpoint"], "get_model_info", timeout=args.timeout)
        reply = generate({"prompt": reference.get("prompt", "The capital of France is")}, target_slot)
        if reply["text"] != new_reference["text"]:
            raise AssertionError("new target bootstrap differs from native reference")
        http(target_slot["endpoint"], "release_memory_occupation", {"tags": ["weights"]}, timeout=300)
        target_slot["parked"] = True
        run.result["target_fresh_restore_s"] = time.monotonic()-started
        run.result["target_fresh_restore"] = {"flow": flow, "reply": reply, "timing": summarize(target_metrics, 2)}
        root_session = out / "root_session.json"
        write(root_session, {"nodes": nodes, "env": run.env, "members": run.members,
                             "endpoints": run.endpoints, "handoff": str(out / "handoff")})
        write(out / "session.json", {"root_session": str(root_session), "models": {"B": model},
            "nodes": nodes, "endpoints": run.endpoints, "members": run.members, "profile": run.cluster.profile,
            "handoff": str(out / "handoff"), "prompt": reference.get("prompt", "The capital of France is"),
            "slots": {"B": source_slot, "target_a": source_slot, "target_b": target_slot},
            "new_engine_processes": [[n, str(p)] for n, p in (source_handle, target_handle)],
            "basic_owned_processes": [[n, str(p)] for n, p in run.processes], "basic_owned_containers": run.owned_containers,
            "basic_ownership_run": str(out), "basic_container_namespace": name})
        run.result.update(status="passed", overlay_session=str(out / "session.json"),
                          measurement_status="bootstrap_only", physical_peak_verified=False)
    except BaseException as exc:
        import traceback
        run.result.update(status="failed", error=repr(exc), traceback=traceback.format_exc())
        cleanup_failure()
    finally:
        run.save()
    return run.result


def cleanup_bootstrap(session, out):
    """Clean only process specs and containers owned by one basic bootstrap."""
    state = load_state(session)
    result_dir = new_run(out)
    owner_root = Path(state["basic_ownership_run"]).resolve()
    namespace = state["basic_container_namespace"]
    expected = "zjn-fluid-basic-" + hashlib.sha256(str(owner_root).encode()).hexdigest()[:14]
    if RESULT_ROOT.resolve() not in owner_root.parents or namespace != expected:
        raise ValueError("invalid bootstrap ownership namespace")
    specs = sorted(state["basic_owned_processes"], key=lambda h: ("fresh_engine" not in Path(h[1]).name, h[1]))
    result = {"status": "running", "operations": [], "scope": "this basic bootstrap only; old mesh untouched"}
    for node, path in specs:
        spec = Path(path).resolve()
        if owner_root != spec.parent:
            raise ValueError("process spec outside bootstrap run")
        row = node_command(node, state, ["python3", str(ROOT / "codex_coding/src/task2/cluster/process.py"), "stop", "--spec", str(spec)], timeout=45)
        result["operations"].append(row)
        write(result_dir / "summary.json", result)
        if row.get("returncode") != 0:
            result["status"] = "failed"
            write(result_dir / "summary.json", result)
            return result
    for node, container in state["basic_owned_containers"]:
        if container != namespace:
            raise ValueError("container outside bootstrap namespace")
        row = host_command(node, ["docker", "rm", "-f", container])
        result["operations"].append(row)
        if row.get("returncode") != 0:
            result["status"] = "failed"
            write(result_dir / "summary.json", result)
            return result
    result["status"] = "passed"
    write(result_dir / "summary.json", result)
    return result
