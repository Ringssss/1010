"""Bounded fresh Torch/HIP initialization and one 1MiB copy in an existing container."""
import argparse
import ctypes
import ctypes.util
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

ROOT = Path(__file__).resolve().parents[6]
RESULT_ROOT = ROOT / "codex_coding/results/v0.1.23.3/task2/three_node_recovery"
PAYLOAD = 1 << 20
GUARD = 2 << 30
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
    os.killpg(owner["pgid"], signal.SIGTERM)
    evidence["signals"].append("TERM")
    try:
        child.wait(timeout=0.75)
    except subprocess.TimeoutExpired:
        actual = proc(child.pid)
        if not exact(actual, owner) or boot_id() != expected_boot:
            return {**evidence, "reason": "identity changed before KILL; no further signal"}
        os.killpg(owner["pgid"], signal.SIGKILL)
        evidence["signals"].append("KILL")
        try:
            child.wait(timeout=0.75)
        except subprocess.TimeoutExpired:
            return {**evidence, "reason": "exact process did not exit after KILL", "after": proc(child.pid)}
    remaining = group_members(owner["pgid"])
    return {**evidence, "passed": not remaining, "after": proc(child.pid), "pending_group_members": remaining}


def bounded(argv, env, out, deadline=15):
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
        child.wait(timeout=max(0, deadline-(time.monotonic_ns()-start)/1e9))
    except subprocess.TimeoutExpired:
        timed_out = True
    except Exception as exc:
        registration_error = repr(exc)
    finally:
        clean = cleanup(child, owner, expected_boot, namespace)
    result = {"pid": child.pid, "owner": owner, "returncode": child.poll(), "timed_out": timed_out,
              "registration_error": registration_error,
              "hard_deadline_seconds": deadline, "elapsed_ns": time.monotonic_ns()-start,
              "cleanup": clean, "actual_after": proc(child.pid)}
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


def worker(args):
    result = {"status": "starting", "gpu_operations": True, "payload_bytes": PAYLOAD if args.stage == "copy" else 0,
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
    parser.add_argument("--stage", choices=("init", "copy"), default="init")
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
                    "hard_deadline_seconds": 15, "stage": args.stage,
                    "expected_container_hostname": args.expected_container_hostname,
                    "payload_bytes": PAYLOAD if args.stage == "copy" else 0, "guard_bytes": GUARD,
                    "performance_sample": False, "owner_exclusivity": "unknown; functional only",
                    "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    write(args.out / "registration.json", registration)
    env = dict(os.environ)
    for key in ("HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES"):
        env.pop(key, None)
    if args.visibility == "selected":
        env.update(HIP_VISIBLE_DEVICES=str(args.gpu), CUDA_VISIBLE_DEVICES=str(args.gpu))
    argv = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], "--worker"]
    process = bounded(argv, env, args.out)
    path = args.out / "child.result.json"
    payload = json.loads(path.read_text()) if path.exists() else None
    passed = process["returncode"] == 0 and not process["registration_error"] and not process["timed_out"] and process["cleanup"]["passed"] and payload is not None and payload["status"] == "passed"
    summary = {"passed": passed, "process": process, "child_result": payload,
               "runtime_scope": "single selected BDF fresh initialization" + (" and full 1MiB copy" if args.stage == "copy" else "; zero payload"),
               "xml_runtime_accepted": False, "performance_ready": False}
    write(args.out / "summary.json", summary)
    print(json.dumps({"out": str(args.out), "passed": passed}))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
