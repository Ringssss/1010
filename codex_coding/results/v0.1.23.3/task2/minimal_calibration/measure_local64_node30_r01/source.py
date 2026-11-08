"""Bounded NUMA H2D and existing RCCL broadcast measurements; parent launches."""
import argparse
import ctypes
import ctypes.util
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import socket
import sys
import threading
import time

SCHEMA = "fluidckpt.minimal_calibration.measurement.v1"
CHUNK = 8 << 20
ENV = {"NCCL_NET_PLUGIN": "librccl-net-shca.so", "HSA_FORCE_FINE_GRAIN_PCIE": "1",
       "NCCL_NET": "IBext_v8", "NCCL_IB_HCA": "shca_0", "NCCL_SOCKET_IFNAME": "ib0"}


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_module(path):
    spec = importlib.util.spec_from_file_location("verified_runtime_r03", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate(config):
    if config["node"] not in ("node28", "node30"):
        raise ValueError("only authorized nodes 28/30")
    if config["primitive"] not in ("local_numa_h2d", "remote_numa_h2d_rccl_broadcast"):
        raise ValueError("unsupported primitive")
    if config["payload_bytes"] not in (64 << 20, 512 << 20):
        raise ValueError("minimal workpoints are 64/512MiB")
    if config.get("iterations", 1) != 1 or config.get("flow_count", 1) != 1:
        raise ValueError("one completed transfer sample per configuration")
    if config["load_scope"] not in ("observed_under_shared_load", "guarded_candidate_scope"):
        raise ValueError("load scope must be explicit")
    if not config.get("owner_refs") or not config.get("guard_before_ref"):
        raise ValueError("current owners and protection references required")
    if config["target_resource_id"] != config["node"] + "/pci/" + config["bdf"]:
        raise ValueError("target resource ID must use registered physical PCI identity")
    if config["source_resource_id"] != config["source_node"] + "/numa/" + str(config["source_numa"]):
        raise ValueError("source resource ID differs from NUMA binding")
    if config["primitive"].startswith("remote"):
        if config["rank"] not in (0, 1) or config["source_node"] != "node28":
            raise ValueError("remote source rank0=node28, receiver rank1=node30")
        if config["node"] != ("node28" if config["rank"] == 0 else "node30"):
            raise ValueError("remote rank/node mapping differs")
        address = config["master"]
        if not address.startswith("tcp://12.11.3.28:"):
            raise ValueError("use authorized two-node rendezvous")
        if not config.get("xml_manifest"):
            raise ValueError("owned XML manifest required")
        if not config.get("source_gpu_resource_id", "").startswith("node28/pci/"):
            raise ValueError("source GPU staging resource must be identified")
    elif config["source_node"] != config["node"]:
        raise ValueError("local H2D must stay on one node")
    if config.get("host_guard_bytes", 2 << 30) < 2 << 30 or config.get("gpu_guard_bytes", 2 << 30) < 2 << 30:
        raise ValueError("do not reduce 2GiB resource reserves")
    return True


def snapshot(config):
    def read(path):
        try:
            return Path(path).read_text().strip()
        except OSError as exc:
            return {"error": repr(exc)}
    cgroup = read("/proc/self/cgroup")
    cgroot = Path("/sys/fs/cgroup")
    # Containers can expose either the cgroup namespace root or the host hierarchy.
    if isinstance(cgroup, str):
        row = next((v.split(":", 2)[2] for v in cgroup.splitlines() if v.startswith("0::")), "/")
        if (cgroot / row.lstrip("/") / "memory.current").exists():
            cgroot = cgroot / row.lstrip("/")
    gpu = Path("/sys/bus/pci/devices") / config["bdf"]
    return {"monotonic_ns": time.monotonic_ns(), "proc_status": read("/proc/self/status"),
            "cgroup": cgroup, "cgroup_memory": {key: read(cgroot / key) for key in
             ("memory.current", "memory.max", "memory.peak", "memory.events", "pids.current")},
            "sysfs_gpu_used_bytes": read(gpu / "mem_info_vram_used"),
            "sysfs_gpu_total_bytes": read(gpu / "mem_info_vram_total")}


class Sampler:
    def __init__(self, config):
        self.config, self.samples, self.stop = config, [], threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.is_set():
            self.samples.append(snapshot(self.config))
            self.stop.wait(.02)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=1)


def fill_pattern(tensor, nbytes):
    import numpy as np
    raw = (np.arange(CHUNK, dtype=np.uint32) % 251).astype(np.uint8)
    digest = hashlib.sha256()
    for off in range(0, nbytes, CHUNK):
        count = min(CHUNK, nbytes - off)
        # Maintain the byte pattern across chunk boundaries without a full-size scratch.
        part = ((raw[:count].astype(np.uint16) + off % 251) % 251).astype(np.uint8)
        import torch
        tensor[off:off + count].copy_(torch.from_numpy(part))
        digest.update(memoryview(part))
    return digest.hexdigest()


def verify(tensor):
    digest, nbytes = hashlib.sha256(), tensor.numel()
    for off in range(0, nbytes, CHUNK):
        part = tensor[off:off + CHUNK].cpu().numpy()
        digest.update(memoryview(part))
        del part
    return digest.hexdigest(), nbytes


def runtime_evidence(args, rt, config, result, topology, manifest):
    dump = args.out / "topology.dump.xml"
    raw = dump.read_bytes()
    view, report = rt.dump_parse_view(raw)
    (args.out / "topology.parse_view.xml").write_bytes(view)
    rt.write(args.out / "topology.parse_view.json", report)
    communication = rt.log_evidence((args.out / "nccl.log").read_bytes(), os.getpid(), config["rank"],
                                    topology, "cross", 2, 0, config["bdf"], config["physical_gpu"], "IBext_v8")
    gpu = rt.dump_check(topology.read_bytes(), view, config["bdf"], config["rank"], 0, communication)
    net = rt.net_dump_check(topology.read_bytes(), view, communication)
    result["runtime_path"] = {"communication": communication, "gpu_mapping": gpu,
                              "net_mapping": net, "raw_dump_sha256": hashlib.sha256(raw).hexdigest(),
                              "owned_xml_sha256": manifest["sha256"], "dump_derivation": report}
    if not all(x["passed"] for x in (communication, gpu, net)):
        raise RuntimeError("actual communicator/GPU/NIC/input XML evidence failed")


def worker(args, config, rt):
    from types import SimpleNamespace
    started = time.monotonic_ns()
    result = {"schema_version": SCHEMA, "status": "starting", "config": config,
              "config_sha256": canonical_sha(config), "formal_performance_ready": False,
              "capacity_is_physical_upper_bound": False, "samples": [], "costs_ns": {},
              "production_executor_equivalent": False, "planner_profile_eligible": False,
              "executor_scope": "whole-payload NUMA H2D/broadcast microprimitive; production chunk/staging/land measured separately"}
    host, tensor, request, group_ready = None, None, None, False
    before_xml = rt.original_hashes()
    sampler = None
    def phase(name, begin):
        result["costs_ns"][name] = time.monotonic_ns() - begin
    try:
        rt.event(args, "torch_import_enter")
        begin = time.monotonic_ns()
        import torch
        phase("torch_import", begin)
        torch.set_num_threads(1)
        plugin = Path("/opt/rccl-rdma-sharp-plugins/lib/librccl-net-shca.so")
        fingerprint = {"torch": torch.__version__, "torch_hip": torch.version.hip,
            "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "runtime_source_sha256": config["runtime_source_sha256"],
            "HostAllocation_sha256": hashlib.sha256((Path(config["main_lib_root"]) / "fluidckpt/memory/host.py").read_bytes()).hexdigest(),
            "environment": {key: os.environ.get(key) for key in (*ENV, "LD_LIBRARY_PATH", "OMP_NUM_THREADS", "MKL_NUM_THREADS")},
            "BDF": config["bdf"], "physical_gpu": config["physical_gpu"], "numa": config["source_numa"],
            "container_id": config["container_id"], "container_started_at": config["container_started_at"],
            "plugin_sha256": hashlib.sha256(plugin.read_bytes()).hexdigest() if plugin.exists() else None}
        result["runtime_fingerprint"] = fingerprint
        result["runtime_fingerprint_sha256"] = canonical_sha(fingerprint)
        begin = time.monotonic_ns()
        torch.cuda.set_device(0)
        phase("gpu_init", begin)
        lib = ctypes.CDLL(ctypes.util.find_library("amdhip64") or "libamdhip64.so")
        lib.hipDeviceGetPCIBusId.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
        lib.hipDeviceGetPCIBusId.restype = ctypes.c_int
        bdf = ctypes.create_string_buffer(32)
        if lib.hipDeviceGetPCIBusId(bdf, len(bdf), 0) != 0 or rt.normalize_bdf(bdf.value.decode()) != config["bdf"]:
            raise RuntimeError("actual HIP BDF differs")
        free, total = torch.cuda.mem_get_info(0)
        nbytes = config["payload_bytes"]
        if free < nbytes + config.get("gpu_guard_bytes", 2 << 30):
            raise MemoryError("fresh HIP free does not cover payload plus guard")
        initial = snapshot(config)
        cg = initial["cgroup_memory"]
        if str(cg["memory.max"]).isdigit() and int(cg["memory.max"]) - int(cg["memory.current"]) < nbytes + (64 << 20) + config.get("host_guard_bytes", 2 << 30):
            raise MemoryError("container headroom does not cover source/scratch plus guard")
        result.update(actual_bdf=config["bdf"], initial_hip_free_bytes=free,
                      initial_hip_total_bytes=total, before_resources=initial)
        rt.event(args, "init_admitted")
        sampler = Sampler(config)
        sampler.__enter__()
        remote = config["primitive"].startswith("remote")
        topology, manifest = None, None
        if remote:
            import torch.distributed as dist
            xml_args = SimpleNamespace(xml_manifest=Path(config["xml_manifest"]), bdf=config["bdf"], node=config["node"])
            topology, manifest = rt.xml_input(xml_args)
            fingerprint["owned_xml_sha256"] = manifest["sha256"]
            result["runtime_fingerprint_sha256"] = canonical_sha(fingerprint)
            begin = time.monotonic_ns()
            dist.init_process_group("nccl", init_method=config["master"], rank=config["rank"], world_size=2,
                                    timeout=datetime.timedelta(seconds=60))
            group_ready = True
            phase("communicator_setup", begin)
            free, _ = torch.cuda.mem_get_info(0)
            if free < nbytes + (2 << 30):
                raise MemoryError("post-communicator HIP free below payload plus guard")
        begin = time.monotonic_ns()
        tensor = torch.zeros(nbytes, dtype=torch.uint8, device="cuda:0")
        phase("target_or_source_staging_allocation", begin)
        expected = None
        if not remote or config["rank"] == 0:
            from fluidckpt.memory.host import HostAllocation
            begin = time.monotonic_ns()
            host = HostAllocation(nbytes, backend="numa_registered", numa=config["source_numa"])
            phase("host_allocate_touch_register", begin)
            result["host_allocation"] = host.info()
            begin = time.monotonic_ns()
            expected = fill_pattern(host.tensor, nbytes)
            phase("prepare_pattern_and_full_host_SHA", begin)
            result["expected_sha256"] = expected
        # Warmup precedes the measured transfer; destination is reset after warmup.
        begin = time.monotonic_ns()
        if host is not None:
            tensor.copy_(host.tensor, non_blocking=True)
        if remote:
            dist.broadcast(tensor, src=0)
        torch.cuda.synchronize(0)
        phase("warmup", begin)
        if not remote or config["rank"] == 1:
            tensor.zero_()
        torch.cuda.synchronize(0)
        if remote:
            request = torch.zeros(1, dtype=torch.uint8, device="cuda:0")
            dist.barrier()
            torch.cuda.synchronize(0)
        torch.cuda.reset_peak_memory_stats(0)
        result["before_transfer_resources"] = snapshot(config)
        transfer_start = time.monotonic_ns()
        if remote:
            # The receiver starts its clock before issuing a causal request. The
            # source's H2D occurs after this request, so the receiver interval
            # includes H2D, request overhead, RCCL transport and target completion.
            dist.broadcast(request, src=1)
        if host is not None:
            tensor.copy_(host.tensor, non_blocking=True)
            torch.cuda.synchronize(0)
        if remote:
            dist.broadcast(tensor, src=0)
        torch.cuda.synchronize(0)
        transfer_end = time.monotonic_ns()
        result["costs_ns"]["completed_transfer"] = transfer_end - transfer_start
        measured_rank = not remote or config["rank"] == 1
        result["after_transfer_resources"] = snapshot(config)
        begin = time.monotonic_ns()
        actual, count = verify(tensor)
        phase("verify_full_device_SHA", begin)
        if remote:
            objects = [expected]
            dist.broadcast_object_list(objects, src=0, device=torch.device("cuda:0"))
            expected = objects[0]
        result["correctness"] = {"passed": actual == expected and count == nbytes,
                                 "sha256": actual, "expected_sha256": expected, "verified_bytes": count}
        if not result["correctness"]["passed"]:
            raise RuntimeError("full payload SHA mismatch")
        if measured_rank:
            result["samples"].append({"primitive": config["primitive"], "direction": "host_to_gpu" if not remote else "node28_to_node30",
                "bytes": nbytes, "source_resource_id": config["source_resource_id"], "target_resource_id": config["target_resource_id"],
                "duration_ns": transfer_end - transfer_start, "start_ns": transfer_start, "end_ns": transfer_end,
                "clock_domain": f"{config['node']}:{config['boot_id']}:monotonic",
                "completion": "cuda_device_synchronize", "rate_Bps": nbytes / ((transfer_end-transfer_start)/1e9),
                "timer_scope": "receiver causal request to completed target, includes sender H2D and request" if remote else "NUMA registered H2D to completed target",
                "full_SHA256": actual, "verified_bytes": count, "config_sha256": canonical_sha(config),
                "runtime_fingerprint_sha256": result["runtime_fingerprint_sha256"],
                "load_scope": config["load_scope"], "owner_refs": config["owner_refs"], "capacity_is_physical_upper_bound": False,
                "production_executor_equivalent": False, "planner_profile_eligible": False})
        result["accounting"] = {"logical_payload_bytes": nbytes, "host_backing_bytes": host.allocated_bytes if host is not None else 0,
            "source_gpu_staging_bytes": nbytes if remote and config["rank"] == 0 else 0,
            "target_gpu_bytes": nbytes if measured_rank else 0, "verification_chunk_bytes": CHUNK,
            "pattern_scratch_bound_bytes": 64 << 20, "gpu_peak_allocated_bytes": torch.cuda.max_memory_allocated(0),
            "gpu_peak_reserved_bytes": torch.cuda.max_memory_reserved(0), "runtime_overhead_in_physical_samples": True,
            "memory_peak_scope": "sampled process/container/device usage; cgroup memory.peak can include earlier runs"}
        if remote:
            dist.barrier()
            torch.cuda.synchronize(0)
            runtime_evidence(args, rt, config, result, topology, manifest)
        result["status"] = "passed"
    except BaseException as exc:
        result.update(status="failed", error=repr(exc))
        raise
    finally:
        if sampler is not None:
            sampler.__exit__()
            result["resource_samples"] = sampler.samples
        cleanup_errors = []
        if host is not None:
            try:
                host.close()
            except BaseException as exc:
                cleanup_errors.append({"phase": "host_unregister_release", "error": repr(exc)})
        if group_ready:
            try:
                dist.destroy_process_group()
            except BaseException as exc:
                cleanup_errors.append({"phase": "communicator_destroy", "error": repr(exc)})
        result["cleanup_errors"] = cleanup_errors
        if cleanup_errors:
            result["status"] = "failed"
        tensor, request = None, None
        result["original_xml_preserved"] = before_xml == rt.original_hashes()
        result["worker_elapsed_ns"] = time.monotonic_ns() - started
        result["sampled_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        rt.write(args.out / "child.result.json", result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--validate-config", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    validate(config)
    if args.validate_config:
        print(json.dumps({"config_valid": True, "config_sha256": canonical_sha(config), "gpu_calls": 0}))
        return
    if args.out is None or not args.out.name.startswith("measure_"):
        parser.error("use fresh versioned measure_* output")
    args.out = args.out.resolve()
    if "codex_coding/results/v0.1.23.3/task2/minimal_calibration" not in str(args.out.parent):
        parser.error("output must be inside the current minimal_calibration results")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if Path("/proc/sys/kernel/random/boot_id").read_text().strip() != config["boot_id"] or socket.gethostname() != config["hostname"]:
        parser.error("host boot/hostname identity differs")
    rt_path = Path(config["runtime_source"])
    if hashlib.sha256(rt_path.read_bytes()).hexdigest() != config["runtime_source_sha256"]:
        parser.error("frozen runtime source SHA differs")
    rt = load_module(rt_path)
    sys.path.insert(0, config["main_lib_root"])
    if args.worker:
        worker(args, config, rt)
        return
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "source.py").write_bytes(Path(__file__).read_bytes())
    (args.out / "config.json").write_bytes(args.config.read_bytes())
    rt.write(args.out / "registration.json", {"schema_version": SCHEMA, "config": config, "config_sha256": canonical_sha(config),
             "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "init_deadline_seconds": 60,
             "total_deadline_seconds": 150 if config["primitive"].startswith("remote") else 100,
             "container_identity_source": "parent current docker inspect/guard before launch"})
    env = dict(os.environ)
    for key in ("ROCR_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "HIP_VISIBLE_DEVICES"):
        env.pop(key, None)
    env.update(HIP_VISIBLE_DEVICES=str(config["physical_gpu"]), CUDA_VISIBLE_DEVICES=str(config["physical_gpu"]),
               OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", PYTHONDONTWRITEBYTECODE="1")
    if config["primitive"].startswith("remote"):
        for key, value in ENV.items():
            if os.environ.get(key) != value:
                parser.error("parent must supply verified existing IB configuration: " + key)
        from types import SimpleNamespace
        topology, _ = rt.xml_input(SimpleNamespace(xml_manifest=Path(config["xml_manifest"]), bdf=config["bdf"], node=config["node"]))
        env.update(NCCL_TOPO_FILE=str(topology), NCCL_TOPO_DUMP_FILE=str(args.out / "topology.dump.xml"),
                   NCCL_TOPO_DUMP_FILE_RANK=str(config["rank"]), NCCL_DEBUG="INFO", NCCL_DEBUG_SUBSYS="INIT,GRAPH,NET",
                   NCCL_DEBUG_FILE=str(args.out / "nccl.log"))
    argv = [sys.executable, str(Path(__file__).resolve()), "--config", str(args.config.resolve()), "--out", str(args.out), "--worker"]
    process = rt.bounded(argv, env, args.out, deadline=150 if config["primitive"].startswith("remote") else 100, init_seconds=60)
    child_path = args.out / "child.result.json"
    child = json.loads(child_path.read_text()) if child_path.exists() else None
    passed = bool(process["returncode"] == 0 and process["init_deadline_passed"] and not process["timed_out"] and
                  not process["registration_error"] and process["cleanup"]["passed"] and child and child["status"] == "passed" and child["original_xml_preserved"])
    rt.write(args.out / "summary.json", {"passed": passed, "process": process, "child_result": child,
             "formal_performance_ready": False, "weights_ready_measured": False,
             "scope": "bounded synthetic executor observation; not full-model Tready or hardware capacity"})
    print(json.dumps({"passed": passed, "out": str(args.out)}))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
