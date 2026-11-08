"""Bounded fresh Torch/HIP initialization and one 1MiB copy in an existing container."""
import argparse
import ctypes
import ctypes.util
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import socket
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[6]
RESULT_ROOT = ROOT / "codex_coding/results/v0.1.23.3/task2/three_node_recovery"
PAYLOAD = 1 << 20
GUARD = 2 << 30
ORIGINAL_FILES = ("/usr/local/built-in-508-topo-input-tj-default.xml", "/opt/dtk/rccl/lib/topo_mapping_default.xml")
IDENTITY = ("pid", "start_time", "pgid", "sid", "argv")


def write(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def proc(pid):
    try:
        tail = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        argv = [p.decode() for p in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if p]
        return {"pid": pid, "start_time": tail[19], "state": tail[0],
                "pgid": int(tail[2]), "sid": int(tail[3]), "argv": argv,
                "live": tail[0] != "Z"}
    except FileNotFoundError:
        return {"pid": pid, "live": False, "state": "absent"}


def exact(actual, expected):
    return actual.get("live") and all(actual.get(k) == expected.get(k) for k in IDENTITY)


def group_members(pgid):
    rows = []
    for p in Path("/proc").glob("[0-9]*"):
        try:
            tail = (p / "stat").read_text().rsplit(") ", 1)[1].split()
            if int(tail[2]) != pgid or tail[0] == "Z":
                continue
            row = proc(int(p.name))
            if row.get("live"):
                rows.append(row)
        except FileNotFoundError:
            continue
        except PermissionError:
            # Other users cannot join this new session; their argv is not read.
            continue
    return rows


def cleanup(child, owner, expected_boot, expected_namespace):
    evidence = {"signals": [], "passed": False}
    if boot_id() != expected_boot or os.readlink("/proc/self/ns/pid") != expected_namespace:
        return {**evidence, "reason": "boot or container PID namespace changed; no signal"}
    actual = proc(child.pid)
    members = group_members(child.pid)
    evidence["before"] = actual
    evidence["group_before"] = members
    if not actual["live"]:
        child.poll()
        return {**evidence, "passed": not members, "reason": "leader exited", "after": actual,
                "pending_group_members": members}
    if not all(k in owner for k in IDENTITY):
        return {**evidence, "reason": "live exact owner registration incomplete; no signal"}
    if not exact(actual, owner) or any(r["sid"] != owner["sid"] for r in members):
        return {**evidence, "reason": "exact owner/session mismatch; no signal"}
    try:
        os.killpg(owner["pgid"], signal.SIGTERM)
    except ProcessLookupError:
        child.poll()
        remaining = group_members(child.pid)
        return {**evidence, "passed": not remaining, "reason": "group exited before TERM",
                "after": proc(child.pid), "pending_group_members": remaining}
    evidence["signals"].append("TERM")
    try:
        child.wait(timeout=0.75)
    except subprocess.TimeoutExpired:
        actual = proc(child.pid)
        if not exact(actual, owner) or boot_id() != expected_boot:
            return {**evidence, "reason": "identity changed before KILL; no further signal"}
        try:
            os.killpg(owner["pgid"], signal.SIGKILL)
        except ProcessLookupError:
            child.poll()
        evidence["signals"].append("KILL")
        try:
            child.wait(timeout=0.75)
        except subprocess.TimeoutExpired:
            return {**evidence, "reason": "exact process did not exit after KILL", "after": proc(child.pid)}
    remaining = group_members(owner["pgid"])
    return {**evidence, "passed": not remaining, "after": proc(child.pid), "pending_group_members": remaining}


def bounded(argv, env, out, deadline=15, init_seconds=None):
    expected_boot = boot_id()
    namespace = os.readlink("/proc/self/ns/pid")
    start = time.monotonic_ns()
    with (out / "child.log").open("wb") as log:
        child = subprocess.Popen(argv, env=env, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
    owner = {"pid": child.pid}
    timed_out = False
    registration_error = None
    try:
        owner = proc(child.pid)
        owner.update(boot_id=expected_boot, pid_namespace=namespace)
        write(out / "child.owner.json", owner)
        if not owner.get("live") or owner.get("argv") != argv or owner.get("pgid") != child.pid or owner.get("sid") != child.pid:
            raise RuntimeError("new child ownership registration failed")
        if init_seconds is None:
            child.wait(timeout=max(0, deadline-(time.monotonic_ns()-start)/1e9))
        else:
            admitted = False
            while child.poll() is None:
                events = out / "child.events.jsonl"
                if events.exists():
                    for line in events.read_text().splitlines():
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if row.get("stage") == "init_admitted":
                            if row["monotonic_ns"] - start >= init_seconds * 1e9:
                                raise subprocess.TimeoutExpired(argv, init_seconds)
                            admitted = True
                elapsed = (time.monotonic_ns()-start)/1e9
                if elapsed >= deadline or (elapsed >= init_seconds and not admitted):
                    raise subprocess.TimeoutExpired(argv, init_seconds if not admitted else deadline)
                time.sleep(0.025)
    except subprocess.TimeoutExpired:
        timed_out = True
    except Exception as exc:
        registration_error = repr(exc)
    finally:
        clean = cleanup(child, owner, expected_boot, namespace)
    result = {"pid": child.pid, "owner": owner, "returncode": child.poll(), "timed_out": timed_out,
              "registration_error": registration_error,
              "hard_deadline_seconds": deadline, "elapsed_ns": time.monotonic_ns()-start,
              "init_deadline_seconds": init_seconds,
              "cleanup": clean, "actual_after": proc(child.pid)}
    events = out / "child.events.jsonl"
    init_events = []
    if events.exists():
        result["last_stage"] = None
        for line in events.read_text().splitlines():
            try:
                row = json.loads(line)
                result["last_stage"] = row.get("stage")
                if row.get("stage") == "init_admitted":
                    init_events.append(row["monotonic_ns"])
            except json.JSONDecodeError:
                continue
    result["init_deadline_passed"] = (init_seconds is None or
        bool(init_events and 0 <= min(init_events)-start < init_seconds*1e9))
    write(out / "child.process.json", result)
    return result


def event(args, stage, **values):
    with (args.out / "child.events.jsonl").open("a") as handle:
        handle.write(json.dumps({"stage": stage, "pid": os.getpid(), "monotonic_ns": time.monotonic_ns(), **values}) + "\n")
        handle.flush()


def normalize_bdf(value):
    match = re.fullmatch(r"([0-9a-fA-F]{1,8}):([0-9a-fA-F]{2}):([0-9a-fA-F]{2})\.([0-7])", value)
    if not match:
        raise ValueError("invalid PCI BDF")
    domain, bus, slot, function = match.groups()
    return f"{int(domain, 16):04x}:{bus.lower()}:{slot.lower()}.{function}"


def admit(actual_bdf, expected_bdf, free):
    if normalize_bdf(actual_bdf) != normalize_bdf(expected_bdf):
        raise ValueError("actual HIP BDF differs from selected physical GPU")
    if free <= GUARD + PAYLOAD:
        raise ValueError("actual HIP free does not exceed 2GiB guard plus payload")


def original_hashes():
    result = []
    for name in ORIGINAL_FILES:
        path = Path(name)
        if not path.exists():
            result.append({"path": name, "status": "missing"})
        else:
            if path.stat().st_size > 16 << 20:
                raise ValueError("original XML exceeds fingerprint bound")
            result.append({"path": name, "status": "present", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return result


def xml_input(args):
    manifest = json.loads(args.xml_manifest.read_text())
    path = Path(manifest["input_file"]).resolve()
    if (path.parent.parent != RESULT_ROOT or not path.parent.name.startswith("probe_xml_")
            or manifest["node"] != args.node or not path.name.endswith(".physical.gdr0.xml")):
        raise ValueError("require this node's owned physical XML")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
        raise ValueError("owned XML differs from manifest SHA")
    if any(e.get("gdr") != "0" for e in ET.fromstring(raw).iter("gpu")):
        raise ValueError("only conservative gdr0 input admitted")
    return path, manifest


def gpu_paths(raw):
    rows = {}
    def visit(element, cpu=None, chain=()):
        if element.tag == "cpu":
            cpu = dict(element.attrib)
        if element.tag == "pci":
            chain = (*chain, normalize_bdf(element.attrib["busid"]))
        if element.tag == "gpu":
            if not chain or cpu is None or chain[-1] in rows:
                raise ValueError("missing or duplicate GPU ancestry")
            rows[chain[-1]] = {"cpu": cpu, "chain": list(chain), "gpu": dict(element.attrib)}
        for child in element:
            visit(child, cpu, chain)
    visit(ET.fromstring(raw))
    return rows


def dump_check(input_raw, dump_raw, bdf, rank, device):
    want, got = gpu_paths(input_raw).get(bdf), gpu_paths(dump_raw).get(bdf)
    checks = {"selected_bdf_present": bool(want and got),
              "full_pci_chain": bool(want and got and want["chain"] == got["chain"]),
              "numa": bool(want and got and want["cpu"]["numaid"] == got["cpu"].get("numaid")),
              "affinity": bool(want and got and want["cpu"]["affinity"] == got["cpu"].get("affinity")),
              "rank": bool(got and got["gpu"].get("rank") == str(rank)),
              "logical_device": bool(got and got["gpu"].get("dev") == str(device)),
              "gdr0_policy": bool(got and got["gpu"].get("gdr") == "0")}
    return {"passed": all(checks.values()), "checks": checks, "input": want, "actual": got,
            "scope": "selected BDF/CPU/PCI ancestry and gdr0 policy only"}


def log_evidence(raw, pid, rank, xml, scope, world_size):
    # Preserve the existing local_xml ownership pattern and loading checks.
    own = [line for line in raw.decode(errors="replace").splitlines()
           if re.search(rf":{pid}:\d+\s+\[{rank}\]", line)]
    loaded = [line for line in own if re.search(r"Loading topology file " + re.escape(str(xml)) + r"(?=\s|$)", line)]
    fallback = [line for line in own if "Loading topology mapping file" in line]
    transport = [line for line in own if re.search(r"via (?:P2P|SHM|NET)/?", line)]
    classes = sorted(set(re.findall(r"via (P2P|SHM|NET)", "\n".join(transport))))
    communicator = [line for line in own if re.search(rf"rank {rank} nRanks {world_size} nNodes \d+\b", line)]
    node_counts = {int(re.search(r"nNodes (\d+)", line).group(1)) for line in communicator}
    topology_scope = (node_counts == {1}) if scope == "local" else bool(node_counts and min(node_counts) > 1 and "NET" in classes)
    return {"passed": bool(loaded and transport and not fallback and topology_scope),
            "pid": pid, "rank": rank, "input_loading_lines": loaded, "fallback_mapping_lines": fallback,
            "transport_lines": transport, "transport_classes": classes, "communicator_lines": communicator,
            "actual_node_counts": sorted(node_counts), "scope": scope,
            "scope_limit": "actual owned communicator channel only; no rate or GDR-capability claim"}


def collective(args, torch, result, device):
    import torch.distributed as dist
    group_ready = False
    topology, manifest = xml_input(args)
    before = original_hashes()
    result.update(rank=args.rank, world_size=args.world_size, master=args.master,
                  owned_xml_sha256=manifest["sha256"], original_files_before=before,
                  effective_communication_environment={k: v for k, v in os.environ.items() if k.startswith(("NCCL_", "RCCL_"))})
    try:
        event(args, "communicator_enter", rank=args.rank, world_size=args.world_size)
        dist.init_process_group("nccl", init_method=args.master, rank=args.rank, world_size=args.world_size,
                                timeout=datetime.timedelta(seconds=20))
        group_ready = True
        event(args, "communicator_exit")
        free, total = torch.cuda.mem_get_info(device)
        result.update(hip_free_before_payload_bytes=free, hip_total_before_payload_bytes=total)
        admit(result["actual_bdf"], args.bdf, free)
        event(args, "payload_admitted", actual_hip_free_bytes=free)
        raw = bytearray((i % 251 for i in range(PAYLOAD)))
        expected = hashlib.sha256(raw).hexdigest()
        event(args, "payload_allocation_enter", payload_bytes=PAYLOAD)
        tensor = (torch.frombuffer(raw, dtype=torch.uint8).to(device=f"cuda:{device}") if args.rank == 0
                  else torch.zeros(PAYLOAD, dtype=torch.uint8, device=f"cuda:{device}"))
        event(args, "broadcast_enter")
        dist.broadcast(tensor, src=0)
        torch.cuda.synchronize(device)
        event(args, "broadcast_exit")
        output = tensor.cpu().numpy().tobytes()
        actual = hashlib.sha256(output).hexdigest()
        result["correctness"] = {"passed": len(output) == PAYLOAD and actual == expected,
                                 "verified_bytes": len(output), "sha256": actual, "expected_sha256": expected}
        if not result["correctness"]["passed"]:
            raise RuntimeError("NCCL full 1MiB buffer SHA failed")
        dump = args.out / "topology.dump.xml"
        if dump.resolve() == topology:
            raise ValueError("dump cannot overwrite input")
        result["topology_dump"] = {"path": str(dump), "sha256": hashlib.sha256(dump.read_bytes()).hexdigest()}
        result["selected_gpu_mapping"] = dump_check(topology.read_bytes(), dump.read_bytes(), args.bdf, args.rank, device)
        if not result["selected_gpu_mapping"]["passed"]:
            raise RuntimeError("actual selected GPU dump differs from physical input")
        result["communication_logs"] = log_evidence((args.out / "nccl.log").read_bytes(), os.getpid(), args.rank,
                                                    topology, args.scope, args.world_size)
        if not result["communication_logs"]["passed"]:
            raise RuntimeError("owned communicator input-loading/transport/scope evidence failed")
        dist.barrier()
        torch.cuda.synchronize(device)
        del tensor
        torch.cuda.empty_cache()
        result["xml_runtime_observed"] = True
        event(args, "collective_verified")
    finally:
        result["original_files_after"] = original_hashes()
        result["original_files_preserved"] = before == result["original_files_after"]
        result["owned_xml_preserved"] = hashlib.sha256(topology.read_bytes()).hexdigest() == manifest["sha256"]
        event(args, "communicator_teardown_enter")
        if group_ready:
            dist.destroy_process_group()
        event(args, "communicator_teardown_exit")
    if not result["original_files_preserved"] or not result["owned_xml_preserved"]:
        raise RuntimeError("XML fingerprints changed")


def worker(args):
    result = {"status": "starting", "gpu_operations": True, "payload_bytes": PAYLOAD if args.stage != "init" else 0,
              "performance_sample": False, "requested_node": args.node, "requested_gpu": args.gpu,
              "requested_bdf": args.bdf, "logical_gpu": 0 if args.visibility == "selected" else args.gpu}
    event(args, "child_enter", core_limit=list(resource.getrlimit(resource.RLIMIT_CORE)),
          process=proc(os.getpid()), boot_id=boot_id(), hostname=socket.gethostname(),
          pid_namespace=os.readlink("/proc/self/ns/pid"),
          visibility={k: os.environ.get(k) for k in ("HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES")})
    try:
        event(args, "torch_import_enter")
        import torch
        event(args, "torch_import_exit", torch_version=torch.__version__, torch_hip=torch.version.hip)
        torch.set_num_threads(1)
        device = result["logical_gpu"]
        event(args, "set_device_enter", logical_gpu=device)
        torch.cuda.set_device(device)
        event(args, "set_device_exit")
        event(args, "hip_bdf_enter")
        lib = ctypes.CDLL(ctypes.util.find_library("amdhip64") or "libamdhip64.so")
        lib.hipDeviceGetPCIBusId.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
        lib.hipDeviceGetPCIBusId.restype = ctypes.c_int
        buf = ctypes.create_string_buffer(32)
        rc = lib.hipDeviceGetPCIBusId(buf, len(buf), device)
        if rc != 0:
            raise RuntimeError(f"hipDeviceGetPCIBusId returned {rc}")
        result["actual_bdf"] = normalize_bdf(buf.value.decode())
        event(args, "hip_bdf_exit", actual_bdf=result["actual_bdf"])
        event(args, "hip_mem_get_info_enter")
        free, total = torch.cuda.mem_get_info(device)
        result.update(hip_free_bytes=free, hip_total_bytes=total, required_guard_bytes=GUARD,
                      memory_source="fresh torch.cuda.mem_get_info; not sysfs total-used")
        event(args, "hip_mem_get_info_exit", free_bytes=free, total_bytes=total)
        admit(result["actual_bdf"], args.bdf, free)
        event(args, "init_admitted")
        if args.stage == "init":
            result["status"] = "passed"
            return
        if args.stage == "nccl":
            collective(args, torch, result, device)
            result["status"] = "passed"
            return
        event(args, "copy_enter", payload_bytes=PAYLOAD)
        raw = bytearray((i % 251 for i in range(PAYLOAD)))
        source = torch.frombuffer(raw, dtype=torch.uint8)
        tensor = source.to(device=f"cuda:{device}")
        torch.cuda.synchronize(device)
        output = tensor.cpu().numpy().tobytes()
        expected = hashlib.sha256(raw).hexdigest()
        actual = hashlib.sha256(output).hexdigest()
        result["correctness"] = {"passed": len(output) == PAYLOAD and actual == expected,
                                 "verified_bytes": len(output), "sha256": actual, "expected_sha256": expected}
        if not result["correctness"]["passed"]:
            raise RuntimeError("full 1MiB round-trip SHA failed")
        event(args, "copy_exit", correctness=result["correctness"])
        del tensor
        torch.cuda.empty_cache()
        torch.cuda.synchronize(device)
        event(args, "release_exit")
        result["status"] = "passed"
    except BaseException as exc:
        result.update(status="failed", error=repr(exc))
        event(args, "failure", error=repr(exc))
        raise
    finally:
        write(args.out / "child.result.json", result)
        event(args, "child_exit", status=result["status"])


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", choices=("node27", "node28", "node30"), required=True)
    parser.add_argument("--container-id", required=True)
    parser.add_argument("--container-started-at", required=True)
    parser.add_argument("--host-boot-id", required=True)
    parser.add_argument("--expected-container-hostname", required=True)
    parser.add_argument("--gpu", type=int, choices=range(8), required=True)
    parser.add_argument("--bdf", required=True)
    parser.add_argument("--visibility", choices=("all", "selected"), default="all")
    parser.add_argument("--stage", choices=("init", "copy", "nccl"), default="init")
    parser.add_argument("--rank", type=int)
    parser.add_argument("--world-size", type=int, choices=(2, 3, 4))
    parser.add_argument("--master")
    parser.add_argument("--xml-manifest", type=Path)
    parser.add_argument("--scope", choices=("local", "cross"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.out = args.out.resolve()
    if args.out.parent != RESULT_ROOT or not args.out.name.startswith("probe_"):
        parser.error("require a new versioned three_node_recovery/probe_* directory")
    args.bdf = normalize_bdf(args.bdf)
    if not re.fullmatch(r"[0-9a-f]{64}", args.container_id) or not args.container_started_at:
        parser.error("require host-verified full container ID and StartedAt")
    if boot_id() != args.host_boot_id or socket.gethostname() != args.expected_container_hostname:
        parser.error("host boot or hostname mismatch")
    topology = None
    if args.stage == "nccl":
        if args.rank is None or args.world_size is None or not 0 <= args.rank < args.world_size or not args.master or args.xml_manifest is None or args.scope is None:
            parser.error("NCCL requires rank, world-size, bounded tcp master and owned xml-manifest")
        address = urllib.parse.urlparse(args.master)
        if address.scheme != "tcp" or not address.hostname or not address.port or address.hostname in ("node29", "10.211.3.29", "11.211.3.29", "12.11.3.29"):
            parser.error("invalid NCCL tcp rendezvous; node29 excluded")
        topology, manifest = xml_input(args)
    if args.worker:
        worker(args)
        return
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "source.py").write_bytes(Path(__file__).read_bytes())
    registration = {"schema_version": "fluidckpt.fresh_init_gate.v1", "node": args.node,
                    "container_id": args.container_id, "container_started_at": args.container_started_at,
                    "container_identity_source": "host docker inspect, required before invocation",
                    "host_boot_id": args.host_boot_id, "pid_namespace": os.readlink("/proc/self/ns/pid"),
                    "gpu": args.gpu, "bdf": args.bdf, "visibility_policy": args.visibility,
                    "core_dump_limit": list(resource.getrlimit(resource.RLIMIT_CORE)),
                    "hard_deadline_seconds": 45 if args.stage == "nccl" else 60,
                    "init_deadline_seconds": 15 if args.stage == "nccl" else 60, "stage": args.stage,
                    "expected_container_hostname": args.expected_container_hostname,
                    "payload_bytes": PAYLOAD if args.stage != "init" else 0, "guard_bytes": GUARD,
                    "performance_sample": False, "owner_exclusivity": "unknown; functional only",
                    "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    write(args.out / "registration.json", registration)
    env = dict(os.environ)
    for key in ("HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES"):
        env.pop(key, None)
    if args.visibility == "selected":
        env.update(HIP_VISIBLE_DEVICES=str(args.gpu), CUDA_VISIBLE_DEVICES=str(args.gpu))
    if args.stage == "nccl":
        env.update(NCCL_TOPO_FILE=str(topology), NCCL_TOPO_DUMP_FILE=str(args.out / "topology.dump.xml"),
                   NCCL_TOPO_DUMP_FILE_RANK=str(args.rank),
                   NCCL_DEBUG="INFO", NCCL_DEBUG_SUBSYS="INIT,GRAPH,NET", NCCL_DEBUG_FILE=str(args.out / "nccl.log"))
    argv = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], "--worker"]
    process = bounded(argv, env, args.out, deadline=45 if args.stage == "nccl" else 60,
                      init_seconds=15 if args.stage == "nccl" else None)
    path = args.out / "child.result.json"
    payload = json.loads(path.read_text()) if path.exists() else None
    passed = process["returncode"] == 0 and process["init_deadline_passed"] and not process["registration_error"] and not process["timed_out"] and process["cleanup"]["passed"] and payload is not None and payload["status"] == "passed"
    summary = {"passed": passed, "process": process, "child_result": payload,
               "runtime_scope": "single selected BDF fresh initialization" + (" and full 1MiB NCCL broadcast" if args.stage == "nccl" else " and full 1MiB copy" if args.stage == "copy" else "; zero payload"),
               "xml_runtime_accepted": bool(passed and args.stage == "nccl" and payload.get("xml_runtime_observed")),
               "performance_ready": False}
    write(args.out / "summary.json", summary)
    print(json.dumps({"out": str(args.out), "passed": passed}))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
