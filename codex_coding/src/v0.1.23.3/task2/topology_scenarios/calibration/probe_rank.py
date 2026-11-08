"""One bounded disposable rank; never contacts keeper or engine APIs."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import sys
import time

from inventory import ROOT, clock_domain, counters, write
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.benchmarks.topology_calibration import counter_deltas


def digest_tensor(tensor, nbytes, value):
    actual, expected = hashlib.sha256(), hashlib.sha256()
    chunk = 8 << 20
    for start in range(0, nbytes, chunk):
        count = min(chunk, nbytes-start)
        raw = tensor[start:start+count].cpu().numpy().tobytes()
        actual.update(raw)
        expected.update(bytes([value])*count)
    got, want = actual.hexdigest(), expected.hexdigest()
    return {"passed": got == want, "sha256": got, "expected_sha256": want,
            "verified_bytes": nbytes, "scope": "all bytes in the reused synthetic allocation; final iteration",
            "transferred_bytes_scope": "the same verified buffer is transferred once per iteration"}


def run(args):
    # Set the GPU identity before any distributed or IPC import can initialize HIP.
    import torch
    torch.set_num_threads(1)
    torch.cuda.set_device(args.gpu)
    import torch.distributed as dist
    from fluidckpt.profiler.numa import HostBuffer
    data = json.loads(args.protocol.read_text())
    target = args.out / f"rank_{args.rank}.json"
    result = {"status": "starting", "rank": args.rank, "node": args.node, "gpu": args.gpu,
              "pid": os.getpid(), "clock_domain": clock_domain(), "cases": [],
              "core_dump_limit": resource.getrlimit(resource.RLIMIT_CORE)}
    write(target, result)
    device, host, stream = None, None, None
    try:
        dist.init_process_group("gloo", init_method=args.master, rank=args.rank, world_size=4,
                                timeout=datetime.timedelta(seconds=args.collective_timeout))
        groups = []
        for pair in ((0, 2), (1, 3)):
            groups.append(dist.new_group(list(pair), backend="nccl",
                timeout=datetime.timedelta(seconds=args.collective_timeout)))
        before_free, _ = torch.cuda.mem_get_info()
        if before_free < 2 << 30:
            raise MemoryError("physical HBM headroom fell below the admitted 2 GiB")
        nbytes = data["resource_limits"]["synthetic_hbm_bytes_per_gpu"]
        device = torch.empty(nbytes, dtype=torch.uint8, device=f"cuda:{args.gpu}")
        stream = torch.cuda.Stream(device=args.gpu)
        after_free, _ = torch.cuda.mem_get_info()
        result["resource_sampling"] = {"free_before_buffer_bytes": before_free,
            "free_after_buffer_bytes": after_free, "synthetic_buffer_bytes": nbytes,
            "torch_allocated_bytes": torch.cuda.memory_allocated(),
            "torch_reserved_bytes": torch.cuda.memory_reserved(),
            "runtime_overhead_scope": "free memory includes communicator/context and all protected users; paired pre/post driver samples provided"}
        dist.barrier()
        for case in data["cases"]:
            is_network = case["kind"] == "network"
            selected = args.rank % 2 in case["flow_ids"] and (is_network or args.node == case["node"])
            value = 31 + args.rank % 2
            if selected:
                device.fill_(value if (not is_network or args.rank < 2) else 0)
                if not is_network and host is None:
                    host = HostBuffer(nbytes, args.numa)
                    host.tensor.fill_(value)
                    placed = host.placement()
                    if placed != {args.numa: 1.0}:
                        raise RuntimeError(f"host NUMA placement mismatch: {placed}")
                    result["host_buffer"] = {"bytes": nbytes, "numa": args.numa,
                                             "sampled_placement": placed, "registered": True}
                if not is_network:
                    host.tensor.fill_(value)
            torch.cuda.synchronize()
            dist.barrier()
            idle_before = counters(args.hca, args.ifname)
            time.sleep(.2)
            idle_after = counters(args.hca, args.ifname)
            dist.barrier()

            def operation():
                with torch.cuda.stream(stream):
                    if is_network:
                        dist.broadcast(device, src=args.rank % 2, group=groups[args.rank % 2])
                    else:
                        device.copy_(host.tensor, non_blocking=True)
                stream.synchronize()

            warm_start = time.monotonic_ns()
            if selected:
                operation()
            warm_end = time.monotonic_ns()
            is_receiver = selected and (not is_network or args.rank >= 2)
            if is_receiver:
                # A skipped measured transfer must leave a digest mismatch, not warmed data.
                device.fill_(0)
                torch.cuda.synchronize()
            dist.barrier()
            nic_before = counters(args.hca, args.ifname)
            dist.barrier()
            intervals = []
            begin = time.monotonic_ns()
            if selected:
                for _ in range(case["iterations"]):
                    start = time.monotonic_ns()
                    operation()
                    intervals.append([start, time.monotonic_ns()])
            end = time.monotonic_ns()
            nic_after = counters(args.hca, args.ifname)
            free_during, _ = torch.cuda.mem_get_info()
            dist.barrier()
            flow = None
            if selected:
                check = digest_tensor(device, nbytes, value)
                if not check["passed"]:
                    raise RuntimeError("synthetic allocation SHA256 failed")
                flow = {"flow_id": args.rank % 2, "src": args.rank % 2 if is_network else args.rank,
                        "dst": args.rank % 2 + 2 if is_network else args.rank,
                        "role": "source" if is_network and args.rank < 2 else "target",
                        "node": args.node, "gpu": args.gpu, "numa": args.numa,
                        "buffer_bytes": nbytes, "iterations": case["iterations"],
                        "bytes": nbytes * case["iterations"], "receiver_start_ns": begin,
                        "receiver_end_ns": end, "clock_domain": clock_domain(),
                        "completion": "cuda_stream_synchronize", "chunk_intervals": intervals,
                        "receiver_reset_before_timing": is_receiver,
                        "correctness": check, "warmup": {"bytes": nbytes,
                            "start_ns": warm_start, "end_ns": warm_end, "excluded_from_timing": True},
                        "nic_counter_delta": counter_deltas(nic_before, nic_after),
                        "idle_background_counter_delta": counter_deltas(idle_before, idle_after),
                        "nic_snapshot_before": nic_before, "nic_snapshot_after": nic_after,
                        "free_hbm_after_case_bytes": free_during,
                        "numa_host_placement": result.get("host_buffer") if not is_network else None}
            result["cases"].append({"case_id": case["case_id"], "flow": flow})
            result["status"] = "running"
            write(target, result)
            dist.barrier()
        result["status"] = "passed"
    except BaseException as exc:
        result.update(status="failed", error=repr(exc))
        raise
    finally:
        # Only this process's allocation and communicator are released.
        try:
            if stream is not None:
                stream.synchronize()
            device = None
            if host is not None:
                host.close()
            if dist.is_initialized():
                dist.destroy_process_group()
        finally:
            write(target, result)


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rank", type=int, required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--numa", type=int, required=True)
    parser.add_argument("--master", required=True)
    parser.add_argument("--hca", required=True)
    parser.add_argument("--ifname", required=True)
    parser.add_argument("--collective-timeout", type=int, default=90)
    parser.add_argument("--deadline", type=int, default=600)
    args = parser.parse_args()
    def expired(signum, frame):
        raise TimeoutError("owned calibration rank deadline expired")
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(args.deadline)
    run(args)


if __name__ == "__main__":
    main()
