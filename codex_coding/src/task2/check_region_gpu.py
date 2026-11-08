"""True partial replicas, cancelled DMA, retirement and generation tests on one idle GPU."""
import argparse
import json
from pathlib import Path
import resource
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "main_lib"))


def main(out):
    import torch
    from fluidckpt.memory import HostAllocation
    from fluidckpt.serving.replicas import ReplicaCatalog
    from fluidckpt.planner.regions import plan_regions
    from fluidckpt.executor.regions import execute_regions
    from run_multipath_fixture import Target
    torch.cuda.set_device(0); torch.set_num_threads(1)
    size = (128 << 20) + 17
    owner = HostAllocation(size, "numa_registered", 2)
    catalog = ReplicaCatalog(extra_budget=64 << 20)
    target = Target(torch.empty(size, dtype=torch.uint8, device="cuda"))
    result = {}
    try:
        pattern = (torch.arange(1 << 20, dtype=torch.int32) % 251).to(torch.uint8)
        for off in range(0, size, pattern.numel()):
            n = min(pattern.numel(), size-off)
            owner.tensor[off:off+n].copy_(pattern[:n]); owner.tensor[off:off+n].add_(off // pattern.numel())
        catalog.publish("base", "fixture", 0, 0, owner.tensor, 2, 0, build={"primary": True})
        extra = catalog.clone("base", "extra", (8 << 20)+3, (32 << 20)+11, 3)
        profile = {"paths": {"2:0": {"GBps": 1, "resources": ["in"]},
                              "3:0": {"GBps": 1, "resources": ["in"]}}, "resources": {"in": 1}}
        ds = [{"tp": 0, "gpu": 0, "dst_w": 0, "identity": "fixture", "nbytes": size,
               "distances": {"2": 10, "3": 20}}]
        planned = plan_regions(ds, catalog.inventory(), profile, "balanced", quantum=1 << 20)
        assert any(o["replica_id"] == "extra" and o["source_offset"] != o["off"] for o in planned["ops"])
        try:
            execute_regions(catalog, planned["ops"], {0: target}, 0, fail_after_chunks=1, chunk_bytes=4 << 20)
            raise AssertionError("injected failure did not propagate")
        except RuntimeError as exc:
            assert "injected copy failure" in str(exc)
            assert all(r["leases"] == 0 for r in catalog.inventory())
            result["copy_failure_drained_and_leases_released"] = True
        result["execution"] = execute_regions(catalog, planned["ops"], {0: target}, 0, chunk_bytes=4 << 20, trace_enabled=True)
        assert torch.equal(target.tensor.cpu(), owner.tensor)
        result["all_bytes_equal"] = True
        result["partial_replica"] = extra
        source_op = next(o for o in planned["ops"] if o["replica_id"] == "extra")
        with catalog.acquire([source_op]):
            assert catalog.retire("extra")["state"] == "RETIRING"
            assert catalog.owned_bytes() > 0
        assert catalog.owned_bytes() == 0
        replacement = catalog.clone("base", "extra", extra["logical_offset"], extra["length"], 3)
        assert replacement["generation"] == 2
        try:
            execute_regions(catalog, planned["ops"], {0: target}, 0)
            raise AssertionError("stale plan accepted")
        except ValueError:
            result["stale_generation_rejected"] = True
        try:
            catalog.clone("base", "too_large", 0, 64 << 20, 3)
            raise AssertionError("budget overflow accepted")
        except MemoryError:
            result["budget_overflow_rejected"] = True
        result["status"] = "passed"
    finally:
        catalog.retire_identity("fixture")
        owner.close()
        Path(out).write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "execution"}, indent=2))


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(); parser.add_argument("--out", required=True)
    main(parser.parse_args().out)
