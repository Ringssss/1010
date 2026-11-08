"""Exercise real HIP IPC capture/restore and failure gates on an isolated idle GPU."""
import argparse
import hashlib
import json
import multiprocessing as mp
import os
import pickle
from pathlib import Path
import resource
import sys
import struct
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "main_lib"))


def producer(handoff, phase, finish):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.environ.update(FLUIDCKPT_HANDOFF=handoff, FLUIDCKPT_TAG=phase)
    import torch
    from fluidckpt.serving.sglang_hook import export_storages
    torch.manual_seed(1 if phase == "capture" else 2)
    model = torch.nn.Linear(1024, 1024, bias=True).cuda()
    model.register_buffer("odd_bytes", torch.arange(17, dtype=torch.uint8, device="cuda"))
    export_storages(model, 0, 0, phase)
    if not finish.wait(90):
        raise TimeoutError("parent did not finish")


def wait_export(path, process):
    start = time.monotonic()
    while not path.exists():
        if process.exitcode is not None:
            raise RuntimeError(f"producer exited {process.exitcode}")
        if time.monotonic() - start > 60:
            raise TimeoutError(str(path))
        time.sleep(0.1)


def counters(path):
    exported = pickle.loads(path.read_bytes())
    values = []
    for entry in exported["entries"]:
        handle = entry["handle"]
        filename = handle[4].decode() if isinstance(handle[4], bytes) else handle[4]
        with open("/dev/shm/" + filename.lstrip("/"), "rb") as stream:
            # This PyTorch build prefixes its refcounted shared allocation with 64 bytes.
            stream.seek(64 + handle[5] * 8)
            values.append(struct.unpack("q", stream.read(8))[0])
    return values


def main(out, backend="torch_pinned_default", numa=None, executor="staged"):
    import torch
    from fluidckpt.serving.keeper import Worker, HbmTarget
    out.mkdir(parents=True, exist_ok=False)
    handoff = str(out / "handoff")
    w = Worker(SimpleNamespace(gpu=0, node="test", handoff=handoff, memory_backend=backend, numa=numa))
    w.rank = 0
    result = {}
    children = []
    try:
        event = mp.Event()
        p = mp.Process(target=producer, args=(handoff, "capture", event))
        p.start(); children.append((p, event))
        capture_path = Path(handoff) / "capture/rank0.pkl"
        wait_export(capture_path, p)
        result["counters_exported"] = counters(capture_path)
        result["capture"] = w.capture("capture", "smoke", timeout=30, retain_live=True)
        result["counters_after_capture"] = counters(capture_path)
        result["capture_verify"] = w.verify("capture", "smoke", 0, "capture")
        result["counters_after_verify"] = counters(capture_path)
        assert result["capture_verify"]["passed"]
        assert result["counters_after_verify"] == [1, 1, 1]
        w.release_live("capture")
        result["counters_after_release"] = counters(capture_path)
        assert result["counters_after_release"] == [0, 0, 0]
        event.set(); p.join(30)
        assert p.exitcode == 0

        event = mp.Event()
        p = mp.Process(target=producer, args=(handoff, "restore", event))
        p.start(); children.append((p, event))
        path = Path(handoff) / "restore/rank0.pkl"
        wait_export(path, p)
        shard = w.shards[("smoke", 0)]
        w.live_targets[("restore", 0)] = HbmTarget(path, shard.index, 0)
        result["before_restore"] = w.verify("restore", "smoke", 0)
        assert not result["before_restore"]["passed"]
        bad = dict(shard.index, absent_tensor=(shard.nbytes, 4))
        try:
            HbmTarget(path, bad, 0)
            raise AssertionError("incomplete skeleton accepted")
        except ValueError:
            result["missing_storage_rejected"] = True

        op = {"kind": "h2d", "dst_w": 0, "tp": 0, "off": 0,
              "n": shard.nbytes, "snap": "missing"}
        flow = {"ops": [op], "into": "hbm", "tag": "restore", "snap": "smoke",
                "retain_live": True, "executor": executor,
                "index": {"0": shard.index}, "meta": {"0": shard.meta}}
        try:
            w.run_flow(flow)
            raise AssertionError("failed local copy accepted")
        except RuntimeError as exc:
            assert isinstance(exc.__cause__, KeyError)
            assert not path.with_suffix(".done").exists()
            result["local_failure_propagated"] = True
        op["snap"] = "smoke"
        result["restore"] = w.run_flow(flow)
        result["restore_verify"] = w.verify("restore", "smoke", 0)
        assert result["restore_verify"]["passed"]
        assert path.with_suffix(".done").exists()
        w.release_live("restore")
        result["restore_counters_after_release"] = counters(path)
        assert result["restore_counters_after_release"] == [0, 0, 0]
        digest = hashlib.sha256(w.shards[("smoke", 0)].buf.numpy()).hexdigest()
        w.save("smoke", str(out / "snapshot"))
        result["drop"] = w.drop("smoke")
        assert not w.shards
        result["reload"] = w.load("smoke", 0, str(out / "snapshot"))
        assert hashlib.sha256(w.shards[("smoke", 0)].buf.numpy()).hexdigest() == digest
        w.drop("smoke")
        with open(out / "snapshot/rank0.bin", "r+b") as stream:
            stream.truncate(13)
        try:
            w.load("smoke", 0, str(out / "snapshot"))
            raise AssertionError("truncated snapshot accepted")
        except IOError:
            assert not w.shards
            result["truncated_snapshot_rejected"] = True
        result["status"] = "passed"
    finally:
        for process, event in children:
            event.set()
            process.join(10)
            if process.is_alive():
                process.kill(); process.join()
        (out / "summary.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    mp.set_start_method("spawn")
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default="keeper_check01")
    parser.add_argument("--backend", default="torch_pinned_default")
    parser.add_argument("--numa", type=int)
    parser.add_argument("--executor", default="staged")
    args = parser.parse_args()
    main(ROOT / "codex_coding/results/task2" / args.run, args.backend, args.numa, args.executor)
