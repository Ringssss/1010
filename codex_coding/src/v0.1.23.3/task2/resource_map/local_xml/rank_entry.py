"""Two-rank local 1MiB broadcast; no keeper APIs or performance estimate."""
import argparse
import ctypes
import ctypes.util
import datetime
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import sys
import time

from common import EXTRA_ENV, GUARD, NODE, ORIGINAL_FILES, PAYLOAD, digest, inventory, owned, write


def event(args, phase, **values):
    with (args.out / f"rank_{args.rank}.events.jsonl").open("a") as handle:
        handle.write(json.dumps({"phase": phase, "rank": args.rank, "pid": os.getpid(),
                                 "monotonic_ns": time.monotonic_ns(), **values}) + "\n")
        handle.flush()


def hash_buffer(tensor, value):
    raw = tensor.cpu().numpy().tobytes()
    if len(raw) != PAYLOAD:
        raise ValueError("unexpected allocation length")
    actual = hashlib.sha256(raw).hexdigest()
    expected = hashlib.sha256(bytes([value]) * PAYLOAD).hexdigest()
    return {"passed": actual == expected, "sha256": actual, "expected_sha256": expected,
            "verified_bytes": len(raw), "expected_value": value, "scope": "entire uint8 1MiB buffer"}


def libraries():
    names = set()
    for line in Path("/proc/self/maps").read_text().splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and fields[5].startswith("/") and any(x in fields[5].lower() for x in ("rccl", "nccl", "shca")):
            names.add(fields[5])
    rows = []
    for name in sorted(names):
        path = Path(name)
        row = {"path": name}
        try:
            if path.stat().st_size > 256 << 20:
                raise ValueError("communication library exceeds 256MiB fingerprint bound")
            row.update(bytes=path.stat().st_size, sha256=digest(path))
        except Exception as exc:
            row["error"] = repr(exc)
        rows.append(row)
    return rows


def environment(args):
    env = {key: value for key, value in sorted(os.environ.items())
           if key.startswith(("NCCL_", "RCCL_", "HIP_", "ROCM_")) or key in EXTRA_ENV}
    return {"rank": args.rank, "gpu": args.rank, "node": NODE, "pid": os.getpid(),
            "process": inventory.proc_state(os.getpid()), "clock_domain": inventory.clock_domain(),
            "pre_torch_import": True, "torch_imported": "torch" in sys.modules,
            "effective_environment": env, "core_dump_limit": list(resource.getrlimit(resource.RLIMIT_CORE)),
            "cpu_affinity": sorted(os.sched_getaffinity(0)),
            "requested_topology_input": {"path": env["NCCL_TOPO_FILE"], "sha256": digest(env["NCCL_TOPO_FILE"])},
            "original_xml": [{"path": p, "sha256": digest(p)} for p in ORIGINAL_FILES]}


def run(args):
    target = args.out / f"rank_{args.rank}.json"
    result = {"status": "starting", "rank": args.rank, "node": NODE, "gpu": args.rank,
              "pid": os.getpid(), "process": inventory.proc_state(os.getpid()), "payload_bytes": PAYLOAD,
              "performance_measurement": False, "clock_domain": inventory.clock_domain()}
    write(target, result)
    dist, group, tensor = None, None, None
    try:
        event(args, "torch_import_enter")
        import torch
        event(args, "torch_import_exit", version=torch.__version__)
        torch.set_num_threads(1)
        event(args, "set_device_enter")
        torch.cuda.set_device(args.rank)
        event(args, "set_device_exit")
        lib = ctypes.CDLL(ctypes.util.find_library("amdhip64") or "libamdhip64.so")
        bdf = ctypes.create_string_buffer(32)
        if lib.hipDeviceGetPCIBusId(bdf, len(bdf), args.rank):
            raise RuntimeError("HIP PCI BDF query failed")
        text = bdf.value.decode().lower()
        result["actual_gpu_bdf"] = f"{int(text.split(':')[0], 16):04x}:" + ":".join(text.split(":")[1:])
        free, total = torch.cuda.mem_get_info()
        result["free_before_communicator_bytes"] = free
        if free <= GUARD:
            raise MemoryError("GPU physical free must exceed 2GiB before communicator")
        import torch.distributed as dist
        event(args, "gloo_enter", master=args.master, world_size=2)
        dist.init_process_group("gloo", init_method=args.master, rank=args.rank, world_size=2,
                                timeout=datetime.timedelta(seconds=40))
        event(args, "gloo_exit")
        event(args, "nccl_group_enter", ranks=[0, 1])
        group = dist.new_group([0, 1], backend="nccl", timeout=datetime.timedelta(seconds=40))
        event(args, "nccl_group_exit")
        free, total = torch.cuda.mem_get_info()
        result["free_before_buffer_bytes"] = free
        if free <= GUARD:
            raise MemoryError("GPU physical free must exceed 2GiB before buffer")
        tensor = torch.empty(PAYLOAD, dtype=torch.uint8, device=f"cuda:{args.rank}")
        tensor.fill_(31 if args.rank == 0 else 0)
        torch.cuda.synchronize()
        dist.barrier()
        event(args, "warmup_enter")
        dist.broadcast(tensor, src=0, group=group)
        torch.cuda.synchronize()
        event(args, "warmup_exit")
        # Reset both roles after warmup: a skipped final transfer must fail target SHA.
        tensor.fill_(31 if args.rank == 0 else 0)
        torch.cuda.synchronize()
        result["pre_transfer_sha"] = hash_buffer(tensor, 31 if args.rank == 0 else 0)
        result["reset_after_warmup"] = True
        if not result["pre_transfer_sha"]["passed"]:
            raise ValueError("pre-transfer reset SHA failed")
        dist.barrier()
        event(args, "functional_broadcast_enter")
        begin = time.monotonic_ns()
        dist.broadcast(tensor, src=0, group=group)
        torch.cuda.synchronize()
        end = time.monotonic_ns()
        event(args, "functional_broadcast_exit")
        result.update(correctness=hash_buffer(tensor, 31), transfer_window_ns=[begin, end],
                      transfer_window_scope="functional operation boundary; not a performance sample",
                      torch_allocated_bytes=torch.cuda.memory_allocated(),
                      torch_reserved_bytes=torch.cuda.memory_reserved(), completion="cuda_synchronize")
        if not result["correctness"]["passed"]:
            raise RuntimeError("full-buffer SHA failed")
        dist.barrier()
        result["status"] = "passed"
    except BaseException as exc:
        result.update(status="failed", error=repr(exc))
        event(args, "failure", error=repr(exc))
        raise
    finally:
        # The driver has a hard external deadline if a native teardown cannot return.
        write(target, result)
        event(args, "teardown_enter")
        try:
            tensor = None
            if dist is not None and dist.is_initialized():
                if group is not None:
                    dist.destroy_process_group(group)
                dist.destroy_process_group()
            event(args, "teardown_exit")
        finally:
            write(args.out / f"rank_{args.rank}.libraries.json", {"rank": args.rank, "pid": os.getpid(),
                  "libraries": libraries(), "scope": "loaded RCCL/NCCL/plugin only; no libtorch hashing"})
            write(target, result)
            event(args, "rank_exit", status=result["status"])


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rank", type=int, choices=(0, 1), required=True)
    parser.add_argument("--master", required=True)
    parser.add_argument("--deadline", type=int, default=120)
    args = parser.parse_args()
    args.out = owned(args.out)
    if not 1 <= args.deadline <= 180 or not args.master.startswith("tcp://127.0.0.1:"):
        parser.error("require bounded local rendezvous")
    def expired(signum, frame):
        event(args, "alarm_expired")
        raise TimeoutError("local rank deadline expired")
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(args.deadline)
    event(args, "early_alarm_armed", seconds=args.deadline, pre_torch_import=True)
    record = environment(args)
    if record["torch_imported"] or record["core_dump_limit"] != [0, 0]:
        raise RuntimeError("pre-import/core evidence failed")
    write(args.out / f"rank_{args.rank}.environment.json", record)
    run(args)


if __name__ == "__main__":
    main()
