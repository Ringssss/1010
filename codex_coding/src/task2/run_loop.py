"""Task2: measure real Qwen inference before and after Keeper DRAM restore.

Run inside the existing SGLang container with /opt/dtk/env.sh sourced.
All child processes belong to this run; existing services are never stopped.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import resource
import signal
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
PROMPTS = ["The capital of France is", "Question: What is 2 + 3?\nAnswer:",
           "Write a Python function that adds two numbers.\n"]


def request(port, path, body=None, timeout=10):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/{path}", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        payload = response.read()
        return json.loads(payload) if payload else {}


def memory():
    out = subprocess.check_output(["hy-smi", "--showmeminfo", "vram", "--json"], text=True)
    return json.loads(out[out.index("{"):])


def used_mib(mem):
    return sum(int(mem[f"card{i}"]["vram Total Used Memory (MiB)"]) for i in (0, 1))


class Experiment:
    def __init__(self, args):
        self.a = args
        self.out = ROOT / "codex_coding/results/task2" / args.run
        self.out.mkdir(parents=True, exist_ok=False)
        self.children = []
        self.events = open(self.out / "events.jsonl", "w", buffering=1)
        self.result = {"task": "task2", "name": "保活闭环", "development": "v0.1.23.2",
                       "run": args.run, "model": args.model, "tp": 2,
                       "status": "running", "phases": {}, "prompts": PROMPTS}
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "main_lib"),
                        HIP_VISIBLE_DEVICES="0,1", HSA_FORCE_FINE_GRAIN_PCIE="1",
                        HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                        SGLANG_KV_LAYOUT_HCU_FA="false", AMD_SERIALIZE_KERNEL="3",
                        NCCL_NET_PLUGIN="librccl-net-shca.so", NCCL_DEBUG="WARN",
                        FLUIDCKPT_MODE="", FLUIDCKPT_HANDOFF=str(self.out / "handoff"))
        self.server_args = ["--model-path", args.model, "--host", "127.0.0.1",
                            "--port", str(args.port), "--tp-size", "2",
                            "--mem-fraction-static", "0.70", "--context-length", "4096",
                            "--max-running-requests", "4", "--disable-cuda-graph",
                            "--attention-backend", "triton", "--page-size", "1",
                            "--moe-runner-backend", "triton", "--max-total-tokens", "8192",
                            "--disable-custom-all-reduce", "--mamba-scheduler-strategy", "extra_buffer",
                            "--max-mamba-cache-size", "32", "--random-seed", "42"]
        self.result["server_args"] = self.server_args
        self.result["environment"] = {k: self.env[k] for k in (
            "HIP_VISIBLE_DEVICES", "AMD_SERIALIZE_KERNEL", "SGLANG_KV_LAYOUT_HCU_FA")}

    def record(self, event, **fields):
        row = dict(time=time.time(), event=event, **fields)
        self.events.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(json.dumps(row, ensure_ascii=False), flush=True)

    def save(self):
        tmp = self.out / "summary.json.tmp"
        tmp.write_text(json.dumps(self.result, ensure_ascii=False, indent=2))
        tmp.replace(self.out / "summary.json")

    def launch(self, name, cmd, env=None):
        self.record("launch", name=name, command=cmd)
        log = open(self.out / f"{name}.log", "w")
        p = subprocess.Popen(cmd, cwd=ROOT, env=env or self.env, stdout=log,
                             stderr=subprocess.STDOUT, start_new_session=True)
        log.close()
        self.children.append(p)
        return p

    def stop(self, p):
        # Signal only the process group created by this experiment.
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait(timeout=10)
        self.children.remove(p)

    def wait_http(self, p, port, path="health", timeout=None):
        start = time.monotonic()
        while time.monotonic() - start < (timeout or self.a.timeout):
            if p.poll() is not None:
                raise RuntimeError(f"process {p.pid} exited with {p.returncode}; inspect phase log")
            try:
                return request(port, path, timeout=2)
            except Exception:
                time.sleep(1)
        raise TimeoutError(f"port {port}/{path}")

    def generate(self):
        replies = []
        for prompt in PROMPTS:
            start = time.monotonic()
            reply = request(self.a.port, "generate", {
                "text": prompt, "sampling_params": {"temperature": 0, "max_new_tokens": 32},
                "return_logprob": True, "logprob_start_len": -1}, timeout=180)
            replies.append({"prompt": prompt, "elapsed_s": time.monotonic() - start, "reply": reply})
        return replies

    def wait_handoffs(self, p, phase):
        start = time.monotonic()
        paths = [self.out / "handoff" / phase / f"rank{rank}.pkl" for rank in range(2)]
        while time.monotonic() - start < self.a.timeout:
            if p.poll() is not None:
                raise RuntimeError(f"{phase} process exited with {p.returncode}")
            if all(path.exists() for path in paths):
                return time.monotonic() - start
            time.sleep(0.2)
        raise TimeoutError(f"{phase} did not export both ranks")

    def server(self, phase, mode=""):
        env = dict(self.env, FLUIDCKPT_MODE=mode, FLUIDCKPT_TAG=phase,
                   FLUIDCKPT_CAPTURE_WAIT="1", FLUIDCKPT_TIMEOUT=str(self.a.timeout))
        cmd = [sys.executable, "-m", "sglang.launch_server"] if not mode else [
            sys.executable, str(ROOT / "codex_coding/src/serving/sglang_fluid_launch.py")]
        if mode == "restore":
            env["FLUIDCKPT_MANIFEST"] = str(self.out / "handoff/capture/derived.json")
        return self.launch(phase, cmd + self.server_args + (
            ["--load-format", "dummy"] if mode == "restore" else []), env)

    def baseline(self):
        self.result["memory_initial"] = memory()
        if used_mib(self.result["memory_initial"]) > 2048:
            raise RuntimeError("selected GPUs are no longer idle")
        start = time.monotonic()
        p = self.server("baseline")
        self.wait_http(p, self.a.port)
        ready_s = time.monotonic() - start
        self.record("baseline_ready", ready_s=ready_s)
        replies = self.generate()
        self.result["phases"]["baseline"] = {
            "ready_s": ready_s, "replies": replies, "memory_ready": memory()}
        self.save()
        self.stop(p)
        time.sleep(3)
        self.result["phases"]["baseline"]["memory_stopped"] = memory()
        self.save()

    def keepers_call(self, op, bodies):
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(request, self.a.keeper_port + gpu, op, bodies[gpu],
                                   self.a.timeout) for gpu in range(2)]
            return [f.result() for f in futures]

    def check_replies(self, actual):
        baseline = self.result["phases"]["baseline"]["replies"]
        matches = [a["reply"]["text"] == b["reply"]["text"] for a, b in zip(actual, baseline)]
        # Token-level scores are retained for diagnosis, exact greedy text is the first gate.
        if len(actual) != len(baseline) or not all(matches):
            raise AssertionError(f"greedy output mismatch: {matches}")
        return matches

    def closed_loop(self):
        mesh = "node27:0-1"
        for gpu in range(2):
            p = self.launch(f"keeper{gpu}", [sys.executable, "-m", "fluidckpt.serving.keeper",
                "--node", "node27", "--gpu", str(gpu), "--port-base", str(self.a.keeper_port),
                "--handoff", str(self.out / "handoff"), "--mesh", mesh,
                "--master", "127.0.0.1:39830"])
            self.wait_http(p, self.a.keeper_port + gpu, "status")
        self.keepers_call("mesh", [{}, {}])
        self.record("keepers_ready")
        self.result["memory_keepers_only"] = memory()

        start = time.monotonic()
        p = self.server("capture", "capture")
        self.wait_handoffs(p, "capture")
        capture_start = time.monotonic()
        rows = self.keepers_call("capture", [
            {"tag": "capture", "snap": "task2", "retain_live": True} for _ in range(2)])
        capture_s = time.monotonic() - capture_start
        self.wait_http(p, self.a.port)
        ready_s = time.monotonic() - start
        if any(len(row) != 1 for row in rows):
            raise AssertionError(f"expected both TP shards: {rows}")
        replies = self.generate()
        self.result["phases"]["capture"] = {"ready_s": ready_s, "capture_s": capture_s, "workers": rows,
            "replies": replies, "memory_ready": memory()}
        self.save()
        self.check_replies(replies)
        verified = self.keepers_call("verify", [
            {"tag": "capture", "snap": "task2", "tp": gpu, "phase": "capture"}
            for gpu in range(2)])
        self.result["phases"]["capture"]["verification"] = verified
        self.save()
        if not all(row["passed"] for row in verified):
            raise AssertionError("captured bytes differ from live model")
        self.record("capture_verified", checked_bytes=sum(x["checked_bytes"] for x in verified))
        self.keepers_call("release_live", [{"tag": "capture"} for _ in range(2)])
        self.stop(p)
        time.sleep(3)
        stopped = memory()
        release = used_mib(self.result["phases"]["capture"]["memory_ready"]) - used_mib(stopped)
        self.result["phases"]["capture"].update(memory_stopped=stopped, released_mib=release)
        self.save()
        if used_mib(stopped) > used_mib(self.result["memory_keepers_only"]) + 2048:
            raise AssertionError("model GPU allocations remain after producer exit")
        self.record("gpu_released", released_mib=release, used_mib=used_mib(stopped))

        indexes = [request(self.a.keeper_port + gpu, "index?snap=task2&tp=" + str(gpu))
                   for gpu in range(2)]
        for repeat in range(self.a.repeats):
            phase = f"restore{repeat + 1}"
            ops = [{"kind": "h2d", "dst_w": gpu, "snap": "task2", "tp": gpu,
                    "off": 0, "n": sum(v[1] for v in indexes[gpu]["index"].values())}
                   for gpu in range(2)]
            body = {"ops": ops, "into": "hbm", "tag": phase, "snap": "task2",
                    "retain_live": True,
                    "index": {str(g): indexes[g]["index"] for g in range(2)},
                    "meta": {str(g): indexes[g]["meta"] for g in range(2)}}
            start = time.monotonic()
            p = self.server(phase, "restore")
            self.wait_handoffs(p, phase)
            skeleton_ready_s = time.monotonic() - start
            flow_start = time.monotonic()
            flow = self.keepers_call("flow", [body, body])
            flow_s = time.monotonic() - flow_start
            self.wait_http(p, self.a.port)
            ready_s = time.monotonic() - start
            replies = self.generate()
            self.result["phases"][phase] = {"ready_s": ready_s, "flow_s": flow_s,
                                          "skeleton_ready_s": skeleton_ready_s, "flow": flow,
                                          "replies": replies, "memory_ready": memory()}
            self.save()
            matches = self.check_replies(replies)
            verified = self.keepers_call("verify", [
                {"tag": phase, "snap": "task2", "tp": gpu} for gpu in range(2)])
            self.result["phases"][phase].update(verification=verified, output_matches=matches)
            self.save()
            if not all(row["passed"] for row in verified):
                raise AssertionError("restored bytes differ from saved snapshot")
            self.record("restore_verified", phase=phase, ready_s=ready_s,
                        output_matches=matches, verified=verified)
            self.keepers_call("release_live", [{"tag": phase} for _ in range(2)])
            self.stop(p)
            time.sleep(3)
            self.result["phases"][phase]["memory_stopped"] = memory()
            self.save()

    def run(self):
        try:
            for port in (self.a.port, self.a.keeper_port, self.a.keeper_port + 1):
                with socket.socket() as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.bind(("127.0.0.1", port))
            if self.a.baseline_from:
                source = ROOT / "codex_coding/results/task2" / self.a.baseline_from / "summary.json"
                previous = json.loads(source.read_text())
                baseline = previous["phases"]["baseline"]
                if (previous["server_args"] != self.server_args or
                        previous["environment"] != self.result["environment"] or
                        "memory_stopped" not in baseline):
                    raise ValueError("baseline settings differ or baseline did not finish")
                self.result["memory_initial"] = memory()
                if used_mib(self.result["memory_initial"]) > 2048:
                    raise RuntimeError("selected GPUs are no longer idle")
                self.result["phases"]["baseline"] = baseline
                self.result["baseline_reused_from"] = str(source)
                self.record("reuse_completed_baseline", source=str(source))
            else:
                self.baseline()
            if self.a.mode == "full":
                self.closed_loop()
            self.result["status"] = "passed"
        except BaseException as exc:
            self.result["status"] = "failed"
            self.result["error"] = repr(exc)
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
    parser.add_argument("--run", required=True)
    parser.add_argument("--mode", choices=["baseline", "full"], default="baseline")
    parser.add_argument("--model", default="/data/node28/shared/zhujianian/Qwen3.6-35B-A3B")
    parser.add_argument("--port", type=int, default=31822)
    parser.add_argument("--keeper-port", type=int, default=39820)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--baseline-from")
    Experiment(parser.parse_args()).run()
