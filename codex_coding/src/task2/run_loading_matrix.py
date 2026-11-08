"""Configurable single-node load/keepalive experiments with shared weight-ready timing."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import time
from types import SimpleNamespace

from run_loop import Experiment, ROOT, memory, used_mib, request

sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.serving.measure import summarize
from fluidckpt.planner.loading import plan


class Matrix(Experiment):
    def __init__(self, config, run):
        args = SimpleNamespace(run=run, model=config["model"], port=config.get("port", 31822),
                               keeper_port=config.get("keeper_port", 39820), timeout=900)
        super().__init__(args)
        self.config = config
        self.topology = json.loads((ROOT / config["topology"]).read_text())
        self.profile = json.loads((ROOT / config["profile"]).read_text())
        if self.profile["topology_fingerprint"] != self.topology["fingerprint"]:
            raise ValueError("profile and topology do not match")
        self.near = {g["logical_gpu"]: g["numa_node"] for g in self.topology["gpus"]}
        self.keeper_processes = []
        self.capture_tag = None
        self.result.update(config=config, samples=[], schema_version=2,
                           topology=self.topology, profile=self.profile)
        identity = hashlib.sha256()
        for name in ("config.json", "model.safetensors.index.json"):
            identity.update((Path(config["model"]) / name).read_bytes())
        for path in sorted(Path(config["model"]).glob("*.safetensors")):
            identity.update(f"{path.name}:{path.stat().st_size}:{path.stat().st_mtime_ns}".encode())
        self.env.update(FLUIDCKPT_MODEL_ID=identity.hexdigest(), FLUIDCKPT_TP_SIZE="2")
        files = list((ROOT / "main_lib/fluidckpt").rglob("*.py")) + list((ROOT / "codex_coding/src/task2").rglob("*.py"))
        files.append(ROOT / "codex_coding/src/serving/sglang_fluid_launch.py")
        self.result["code_sha256"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        self.result["software"] = subprocess.check_output([sys.executable, "-m", "pip", "show", "torch", "sglang", "scipy"], text=True)
        self.result["model_identity"] = identity.hexdigest()
        self.save()

    def cache_snapshot(self):
        files = sorted(Path(self.a.model).glob("*.safetensors"))
        output = subprocess.check_output(["fincore", "--bytes", "--noheadings", "--output", "RES,SIZE"] + [str(p) for p in files], text=True)
        rows = [tuple(map(int, line.split())) for line in output.splitlines()]
        return {"resident_bytes": sum(r for r, _ in rows), "file_bytes": sum(s for _, s in rows), "files": len(rows)}

    def server(self, phase, mode=""):
        env = dict(self.env, FLUIDCKPT_MODE=mode, FLUIDCKPT_TAG=phase,
                   FLUIDCKPT_CAPTURE_WAIT="1", FLUIDCKPT_TIMEOUT=str(self.a.timeout),
                   FLUIDCKPT_METRICS=str(self.out / "metrics" / phase))
        if mode == "restore":
            env["FLUIDCKPT_MANIFEST"] = str(self.out / "handoff" / self.capture_tag / "derived.json")
        cmd = [sys.executable, str(ROOT / "codex_coding/src/serving/sglang_fluid_launch.py")] + self.server_args
        if mode == "restore":
            cmd += ["--load-format", "dummy"]
        return self.launch(phase, cmd, env)

    def keepers_start(self, backend):
        for gpu in range(2):
            command = [sys.executable, "-m", "fluidckpt.serving.keeper", "--node", "node27",
                       "--gpu", str(gpu), "--port-base", str(self.a.keeper_port),
                       "--handoff", str(self.out / "handoff"), "--mesh", "node27:0-1",
                       "--master", "127.0.0.1:39830", "--memory-backend", backend,
                       "--numa", str(self.near[gpu])]
            child = self.launch(f"{backend}_keeper{gpu}", command)
            self.keeper_processes.append(child)
            self.wait_http(child, self.a.keeper_port + gpu, "status")
        self.keepers_call("mesh", [{}, {}])

    def keepers_stop(self):
        self.keepers_call("drop", [{"snap": "task2"}, {"snap": "task2"}])
        for process in self.keeper_processes:
            self.stop(process)
        self.keeper_processes.clear()
        time.sleep(2)

    def host_memory(self):
        result = []
        for gpu in range(2):
            status = request(self.a.keeper_port + gpu, "status")
            fields = {}
            for line in Path(f"/proc/{status['pid']}/smaps_rollup").read_text().splitlines()[1:]:
                key, value = line.split(":", 1)
                fields[key] = int(value.split()[0]) * 1024
            result.append({"gpu": gpu, "memory": fields, "status": status})
        return result

    def make_plan(self, indexes, strategy):
        demands, resources = [], {}
        for gpu, index in enumerate(indexes):
            path = next(row for group in self.profile["scenarios"][f"local{gpu}"]
                        for row in group["rows"] if row["bytes"] == 1 << 30)
            capacity = path["median_GBps"]
            rid = f"local_h2d:{gpu}"
            resources[rid] = capacity
            demands.append({"tp": gpu, "nbytes": sum(v[1] for v in index["index"].values()),
                            "candidates": [{"GBps": capacity, "resources": [rid],
                                "op": {"kind": "h2d", "dst_w": gpu, "snap": "task2"}}]})
        result = plan(demands, resources, strategy, quantum=self.config.get("chunk_bytes", 256 << 20))
        result["profile_fingerprint"] = self.profile["topology_fingerprint"]
        result["scope"] = "single local source per rank; no claim of multi-source planning benefit"
        return result

    def restore_flow(self, indexes, planning, phase, executor):
        body = {"ops": planning["ops"], "into": "hbm", "tag": phase, "snap": "task2",
                "retain_live": True, "executor": executor, "chunk_bytes": self.config.get("chunk_bytes", 256 << 20),
                "index": {str(g): indexes[g]["index"] for g in range(2)},
                "meta": {str(g): indexes[g]["meta"] for g in range(2)}}
        return self.keepers_call("flow", [body, body])

    def sample(self, phase, mode="", executor="staged", planner="fixed_local", backend=None):
        self.result["active_sample"] = {"phase": phase, "mode": mode or "baseline", "backend": backend,
                                        "executor": executor, "planner": planner, "started_wall": time.time()}
        self.save()
        before = self.cache_snapshot()
        started = time.monotonic()
        server = self.server(phase, mode)
        extra = {}
        if mode == "capture":
            self.wait_handoffs(server, phase)
            capture_start = time.monotonic()
            extra["capture"] = self.keepers_call("capture", [
                {"tag": phase, "snap": "task2", "retain_live": True, "timeout": 60} for _ in range(2)])
            extra["capture_s"] = time.monotonic() - capture_start
            if any(len(x) != 1 for x in extra["capture"]):
                raise AssertionError("missing capture rank")
            self.capture_tag = phase
        elif mode == "restore":
            self.wait_handoffs(server, phase)
            indexes = [request(self.a.keeper_port + g, f"index?snap=task2&tp={g}") for g in range(2)]
            planning = self.make_plan(indexes, planner)
            extra["plan"] = planning
            extra["flow"] = self.restore_flow(indexes, planning, phase, executor)
        self.wait_http(server, self.a.port)
        ready_s = time.monotonic() - started
        timing = summarize(self.out / "metrics" / phase, 2)
        replies = self.generate()
        result = {"phase": phase, "mode": mode or "baseline", "backend": backend, "executor": executor,
                  "planner": planner, "timing": timing, "ready_s": ready_s,
                  "cache_before": before, "cache_after": self.cache_snapshot(),
                  "replies": replies, "memory_ready": memory(), **extra}
        self.result["samples"].append(result)
        self.save()
        if "baseline" not in self.result["phases"]:
            self.result["phases"]["baseline"] = {"replies": replies}
        self.check_replies(replies)
        if mode:
            checks = self.keepers_call("verify", [
                {"tag": phase, "snap": "task2", "tp": g, "phase": mode} for g in range(2)])
            result["verification"] = checks
            self.save()
            if not all(c["passed"] for c in checks):
                raise AssertionError("full byte comparison failed")
            self.keepers_call("release_live", [{"tag": phase} for _ in range(2)])
        self.stop(server)
        time.sleep(3)
        result["memory_stopped"] = memory()
        if mode:
            result["keeper_memory"] = self.host_memory()
            if used_mib(result["memory_stopped"]) > 4096:
                raise AssertionError("model HBM remains allocated after inference process exit")
        result["passed"] = True
        self.result["active_sample"] = None
        self.save()
        self.record("sample_passed", phase=phase, load_s=timing["load_s"],
                    rank_s=timing["rank_s"], backend=backend, executor=executor, planner=planner)

    def execute(self):
        try:
            if used_mib(memory()) > 2048:
                raise RuntimeError("selected GPUs are busy")
            # The initial file run is a separately labelled warm-up, never a cold-cache claim.
            self.sample("warmup")
            for backend in self.config["backends"]:
                self.keepers_start(backend)
                self.sample(f"capture_{backend}", "capture", backend=backend)
                for repeat in range(self.config["repeats"]):
                    # Alternate order to expose order effects rather than silently discarding them.
                    if repeat % 2 == 0:
                        self.sample(f"baseline_{backend}_{repeat}")
                    choices = list(self.config["variants"])
                    if repeat % 2:
                        choices.reverse()
                    for variant in choices:
                        name = f"restore_{backend}_{variant['executor']}_{variant['planner']}_{repeat}"
                        self.sample(name, "restore", backend=backend, **variant)
                    if repeat % 2:
                        self.sample(f"baseline_{backend}_{repeat}")
                self.keepers_stop()
            self.result["status"] = "passed"
        except BaseException as exc:
            self.result.update(status="failed", error=repr(exc))
            self.result["failed_sample"] = self.result.get("active_sample")
            self.record("failed", error=repr(exc))
            raise
        finally:
            for child in list(reversed(self.children)):
                self.stop(child)
            self.result["memory_final"] = memory()
            self.save()


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    Matrix(json.loads(Path(args.config).read_text()), args.run).execute()
