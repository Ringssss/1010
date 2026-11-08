"""Read-only CPU/backend/cache and retained-platform evidence for native loading."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
import hashlib
import importlib.metadata
import inspect
import json
import mmap
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "codex_coding/src/v0.1.23.3/task2/basic_scenarios"))
from runtime_support import digest, http, load_state, node_command, host_command, retained_inventory, select_base, source_signature, write

RESULT_ROOT = ROOT / "codex_coding/results/v0.1.23.3/task2/baseline_loading"


def new_output(path):
    path = Path(path).resolve()
    if RESULT_ROOT.resolve() not in path.parents:
        raise ValueError("output must be below versioned baseline_loading results")
    path.mkdir(parents=True, exist_ok=False)
    os.chmod(path, 0o777)
    return path


def checkpoint_files(model):
    model = Path(model)
    index = model / "model.safetensors.index.json"
    paths = [model / name for name in sorted(set(json.loads(index.read_text())["weight_map"].values()))] if index.exists() else sorted(model.glob("*.safetensors"))
    if not paths or any(not p.is_file() for p in paths):
        raise ValueError("complete safetensors checkpoint is required")
    return paths


def parse_json_output(value):
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        for line in reversed(value.splitlines()):
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    pass
        raise ValueError("probe stdout contains no complete JSON value")


def file_cache(model, samples_per_file=256):
    """Query evenly spaced backing pages without faulting checkpoint data into RAM."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
    libc.mincore.restype = ctypes.c_int
    page = mmap.PAGESIZE
    rows = []
    for path in checkpoint_files(model):
        size = path.stat().st_size
        pages = (size + page - 1) // page
        count = min(pages, samples_per_file)
        positions = sorted({i * (pages - 1) // max(1, count - 1) for i in range(count)})
        with path.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_COPY) as mapping:
            anchor = ctypes.c_char.from_buffer(mapping)
            address = ctypes.addressof(anchor)
            flags = []
            try:
                for pos in positions:
                    vec = ctypes.c_ubyte()
                    if libc.mincore(address + pos * page, 1, ctypes.byref(vec)):
                        raise OSError(ctypes.get_errno(), "mincore")
                    flags.append(bool(vec.value & 1))
            finally:
                del anchor
        rows.append({"path": str(path), "bytes": size, "sampled_pages": len(flags), "resident_pages": sum(flags),
                     "resident_fraction": sum(flags) / len(flags), "sample_positions": positions})
    count = sum(r["sampled_pages"] for r in rows)
    resident = sum(r["resident_pages"] for r in rows)
    return {"host": os.uname().nodename, "wall_time": time.time(), "method": "mincore evenly spaced pages; no checkpoint data read",
            "scope": "sampled Linux file-page residency; underlying shared filesystem cache/storage not inferred",
            "files": rows, "checkpoint_bytes": sum(r["bytes"] for r in rows), "sampled_pages": count,
            "resident_pages": resident, "resident_fraction": resident / count}


def warm_files(model):
    started = time.monotonic()
    total = 0
    for path in checkpoint_files(model):
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(16 << 20), b""):
                total += len(block)
    return {"read_bytes": total, "wall_s": time.monotonic() - started, "method": "sequential buffered read; no global cache eviction"}


def backend_identity():
    # Importing loader modules does not initialize a model or execute a GPU op.
    from sglang.srt.model_loader import loader
    from sglang.srt.model_loader import weight_utils
    from sglang.srt.model_executor import model_runner
    packages = {}
    for name in ("sglang", "torch", "transformers", "safetensors"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    distribution = importlib.metadata.distribution("sglang")
    direct_url = distribution.read_text("direct_url.json")
    path = Path(inspect.getsourcefile(loader.DefaultModelLoader.load_model)).resolve()
    git = subprocess.run(["git", "-C", str(path.parent), "rev-parse", "HEAD"], capture_output=True, text=True)
    status = subprocess.run(["git", "-C", str(path.parent), "status", "--porcelain"], capture_output=True, text=True)
    return {"identity": "haiguang_vendor_sglang_native_checkpoint", "packages": packages,
            "loader_path": str(path), "loader_file_sha256": digest(path),
            "loader_callable_sha256": hashlib.sha256(inspect.getsource(loader.DefaultModelLoader.load_model).encode()).hexdigest(),
            "installed_key_sources": {str(Path(module.__file__).resolve()): digest(module.__file__) for module in (loader, weight_utils, model_runner)},
            "installed_source_commit": git.stdout.strip() if git.returncode == 0 else None,
            "package_direct_url": json.loads(direct_url) if direct_url else None,
            "packaged_build_revision": packages["sglang"].rsplit(".g", 1)[-1] if ".g" in packages["sglang"] else None,
            "git_probe": {"returncode": git.returncode, "stderr": git.stderr},
            "worktree_status": status.stdout if status.returncode == 0 else None,
            "not_frozen_upstream_original": True}


def parked_evidence(slot):
    if slot.get("parked") is not True:
        raise ValueError("slot is not explicitly parked")
    command = json.loads(Path(slot["command"]).read_text())
    rows = []
    for rank in range(2):
        path = Path(command["metrics"]) / f"rank{rank}.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        lifecycle = [e for e in events if e["event"] in ("load_begin", "weights_ready", "release_begin", "release_end", "load_failed")]
        if not lifecycle or lifecycle[-1]["event"] != "release_end":
            raise ValueError("latest event does not prove parked state")
        rows.append({"rank": rank, "path": str(path), "sha256": digest(path), "last_event": lifecycle[-1]})
    return {"get_model_info": http(slot["endpoint"], "get_model_info"), "release_evidence": rows}


def inspect_platform(state, model_path, node="node29", probe_path=None):
    workers, replicas = retained_inventory(state)
    base = select_base(state["models"]["B"], replicas)
    slot = state["slots"]["target_b" if node == "node29" else "target_a"]
    script = str(Path(__file__).resolve())
    def remote_probe(mode):
        command = ["bash", "-lc", "ulimit -c 0\nsource /opt/dtk/env.sh >/dev/null 2>&1\nexec " + __import__("shlex").join(["python3", script, mode, "--model-path", str(model_path)])]
        return node_command(node, state, command, timeout=60)
    with ThreadPoolExecutor(4) as pool:
        jobs = {"backend": pool.submit(remote_probe, "backend"), "cache": pool.submit(remote_probe, "cache"),
                "gpu_memory": pool.submit(node_command, node, state, ["hy-smi", "--showmeminfo", "vram", "--json"]),
                "host_memory": pool.submit(host_command, node, ["cat", "/proc/meminfo"])}
        probes = {key: future.result() for key, future in jobs.items()}
    probes["tcp"] = node_command(node, state, ["cat", "/proc/net/tcp", "/proc/net/tcp6"])
    if probe_path:
        write(probe_path, probes)
    for key, probe in probes.items():
        if probe.get("returncode") != 0:
            raise RuntimeError(f"probe failed {key}: {probe}")
    return {"read_only": True, "workers": workers, "base_source_signature": source_signature(base),
            "retained_bytes": sum(s["n"] for s in state["models"]["B"]["sources"]),
            "parked": {name: parked_evidence(s) for name, s in state["slots"].items() if name in ("target_a", "target_b")},
            "node": node, "target_slot": slot, "probes": probes,
            "backend": parse_json_output(probes["backend"]["stdout"]), "cache": parse_json_output(probes["cache"]["stdout"])}


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("platform", "backend", "cache", "warm"))
    p.add_argument("--model-path", type=Path, default=Path("/data/node28/shared/zhujianian/Qwen3.6-35B-A3B"))
    p.add_argument("--session", type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument("--node", choices=("node27", "node29"), default="node29")
    a = p.parse_args()
    if a.mode == "backend":
        result = backend_identity()
    elif a.mode == "cache":
        result = file_cache(a.model_path)
    elif a.mode == "warm":
        result = {"warmup": warm_files(a.model_path), "cache": file_cache(a.model_path)}
    else:
        out = new_output(a.out)
        try:
            result = inspect_platform(load_state(a.session), a.model_path, a.node, out / "raw_probes.json")
            result["status"] = "passed"
        except BaseException as exc:
            write(out / "summary.json", {"status": "failed", "read_only": True, "error": repr(exc)})
            raise
        write(out / "summary.json", result)
        print(json.dumps({"status": result["status"], "out": str(out), "retained_bytes": result["retained_bytes"],
                          "cache_resident_fraction": result["cache"]["resident_fraction"], "backend": result["backend"]}))
        return
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
