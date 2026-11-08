"""Real distinct bytes through two NUMA sources; compare fixed plans and planners."""
import argparse
import json
import multiprocessing as mp
import os
from pathlib import Path
import resource
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "main_lib"))


class Target:
    def __init__(self, tensor): self.tensor = tensor
    def scatter(self, offset, src):
        self.tensor[offset:offset+src.numel()].copy_(src, non_blocking=True)
        return src.numel()


def task_worker(gpu, nbytes, control, barrier, queue):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    import torch
    from fluidckpt.memory import HostAllocation
    from fluidckpt.serving.replicas import ReplicaCatalog
    from fluidckpt.executor.regions import execute_regions
    torch.cuda.set_device(gpu); torch.set_num_threads(1)
    catalog = ReplicaCatalog(extra_budget=2*nbytes)
    primary = HostAllocation(nbytes, "numa_registered", 0)
    pattern = (torch.arange(1 << 20, dtype=torch.int32) % 251).to(torch.uint8)
    for off in range(0, nbytes, pattern.numel()):
        n = min(pattern.numel(), nbytes-off)
        primary.tensor[off:off+n].copy_(pattern[:n])
        primary.tensor[off:off+n].add_((off // pattern.numel()*17+gpu*31) % 256)
    # Every page carries its absolute word offset and rank; repeating a cached chunk cannot pass.
    words = primary.tensor[:nbytes // 8 * 8].view(torch.int64)
    words[::512].copy_(torch.arange(0, words.numel(), 512, dtype=torch.int64) + (gpu+1) * 2**48)
    del words
    rid = f"rank{gpu}/primary"
    catalog.publish(rid, f"fixture-{gpu}", gpu, 0, primary.tensor, 0, gpu, build={"primary": True})
    catalog.clone(rid, f"rank{gpu}/extra", 0, nbytes, 3)
    catalog.clone(rid, f"rank{gpu}/partial", nbytes // 4, nbytes // 2, 3)
    target = Target(torch.empty(nbytes, dtype=torch.uint8, device=f"cuda:{gpu}"))
    queue.put({"gpu": gpu, "replicas": catalog.inventory()})
    try:
        while True:
            message = control.recv()
            if message["command"] == "stop": break
            ops = [o for o in message["plan"]["ops"] if o["dst_w"] == gpu]
            target.tensor.fill_(213); torch.cuda.synchronize()
            barrier.wait(timeout=120)
            begin = time.monotonic_ns()
            result = execute_regions(catalog, ops, {gpu: target}, gpu,
                        parallel=message["parallel"], chunk_bytes=64 << 20,
                        max_inflight=2, paced=message.get("paced", False),
                        trace_enabled=message.get("trace", False)) if ops else None
            end = time.monotonic_ns()
            mismatches = 0
            if ops:
                error = torch.zeros((), dtype=torch.int64, device=f"cuda:{gpu}")
                for off in range(0, nbytes, 16 << 20):
                    n = min(16 << 20, nbytes-off)
                    reference = primary.tensor[off:off+n].to(gpu)
                    error += torch.count_nonzero(target.tensor[off:off+n] != reference)
                mismatches = int(error.item())
                del reference, error
            queue.put({"gpu": gpu, "begin_ns": begin, "end_ns": end, "execution": result,
                       "mismatched_bytes": mismatches, "verified_bytes": nbytes if ops else 0})
    except BaseException as exc:
        barrier.abort()
        queue.put({"gpu": gpu, "error": repr(exc)})
    finally:
        catalog.retire_identity(f"fixture-{gpu}")
        primary.close()


def main(args):
    from fluidckpt.planner.regions import plan_regions
    out = ROOT / "codex_coding/results/task2/stage2" / args.run
    out.mkdir(parents=True, exist_ok=False)
    profile = json.loads((ROOT / args.profile).read_text())
    nbytes = args.gib * 2**30 + 17
    barrier, queue = mp.Barrier(2), mp.Queue()
    children, pipes = [], []
    result = {"task": "task2", "stage": 2, "development": "v0.1.23.2", "run": args.run,
              "status": "running", "rank_bytes": nbytes,
              "environment": {k: os.environ.get(k) for k in ("AMD_SERIALIZE_KERNEL", "HIP_VISIBLE_DEVICES")},
              "timing_scope": "preallocated GPU target; validation excluded; not full model load", "samples": []}
    def save(): (out / "summary.json").write_text(json.dumps(result, indent=2))
    try:
        for gpu in range(2):
            parent, child = mp.Pipe()
            process = mp.Process(target=task_worker, args=(gpu, nbytes, child, barrier, queue))
            process.start(); children.append(process); pipes.append(parent)
        prepared = [queue.get(timeout=180) for _ in children]
        if any("error" in r for r in prepared): raise RuntimeError(str(prepared))
        all_replicas = [r for row in prepared for r in row["replicas"]]
        replicas = [r for r in all_replicas if not r["replica_id"].endswith("/partial")]
        result["replicas"] = all_replicas; result["trace_enabled"] = args.trace; save()
        ds = [{"tp": g, "gpu": g, "dst_w": g, "identity": f"fixture-{g}", "nbytes": nbytes,
               "distances": {"0": 10 if g == 0 else 15, "3": 15 if g == 0 else 10}} for g in range(2)]
        scenarios = []
        # Same plan, serial vs concurrent, and a single-GPU shared-ingress negative control.
        scenarios += [("C1_single_gpu", ds[:1], replicas, s, parallel)
                      for s, parallel in [("nearest", False), ("balanced", False), ("balanced", True), ("resource_lp", True)]]
        local = [r for r in replicas if (r["rank"] == 0 and r["numa"] == 0) or (r["rank"] == 1 and r["numa"] == 3)]
        scenarios += [("C2_local", ds, local, s, True) for s in ("nearest", "resource_lp")]
        scenarios += [("C3_contended", ds, replicas, s, True) for s in ("fixed", "nearest", "fastest", "greedy", "resource_lp")]
        partial = [r for r in all_replicas if not r["replica_id"].endswith("/extra")]
        scenarios += [("C4_partial", ds, partial, s, True) for s in ("nearest", "balanced", "resource_lp")]
        scenarios += [("C4_partial_paced", ds, partial, "resource_lp", True),
                      ("C3_contended_paced", ds, replicas, "resource_lp", True)]
        trials = []
        for name, demands, sources, strategy, parallel in scenarios:
            plan = plan_regions(demands, sources, profile, strategy)
            for repeat in range(args.repeats + 1):
                trials.append((repeat, name, strategy, parallel, plan))
        # Round-wise rotation keeps each strategy away from a fixed position in the sequence.
        ordering = {(s[0], s[3], s[4]): i for i, s in enumerate(scenarios)}
        trials.sort(key=lambda t: (t[0], ordering[t[1], t[2], t[3]] * (1 if t[0] % 2 == 0 else -1)))
        for repeat, name, strategy, parallel, plan in trials:
            for pipe in pipes: pipe.send({"command": "run", "plan": plan, "parallel": parallel,
                                         "trace": args.trace, "paced": name.endswith("_paced")})
            rows = [queue.get(timeout=120) for _ in children]
            if any("error" in row for row in rows): raise RuntimeError(str(rows))
            active = [r for r in rows if r["execution"]]
            duration = (max(r["end_ns"] for r in active)-min(r["begin_ns"] for r in active))/1e9
            if any(r["mismatched_bytes"] for r in rows): raise AssertionError("fixture byte mismatch")
            result["samples"].append({"scenario": name, "strategy": strategy, "parallel": parallel,
                "warmup": repeat == 0, "repeat": repeat, "transfer_s": duration, "plan": plan, "workers": rows})
            save()
        for name, _, _, strategy, parallel in scenarios:
            observed = [s["transfer_s"] for s in result["samples"] if s["scenario"] == name and s["strategy"] == strategy
                        and s["parallel"] == parallel and not s["warmup"]]
            print(json.dumps({"scenario": name, "strategy": strategy, "parallel": parallel,
                              "median_s": statistics.median(observed)}), flush=True)
        result["status"] = "passed"
    except BaseException as exc:
        result.update(status="failed", error=repr(exc)); raise
    finally:
        for pipe in pipes:
            try: pipe.send({"command": "stop"})
            except (BrokenPipeError, OSError): pass
        for process in children:
            process.join(10)
            if process.is_alive(): process.kill(); process.join()
        save()


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    mp.set_start_method("spawn")
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--gib", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--trace", action="store_true")
    main(parser.parse_args())
