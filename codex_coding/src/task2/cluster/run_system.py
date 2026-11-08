"""One-pass cluster infrastructure acceptance: fixtures, TP2 dual-node and three-node restore."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import resource
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.cluster.control import Cluster, request
from fluidckpt.serving.measure import summarize
from fluidckpt.serving.handoff import wait_for_exports

NODES = {
    "node27": {"ip": "12.11.3.27", "container": "zhujianian-fluidckpt", "gpus": [0, 1, 2, 3]},
    "node28": {"ip": "12.11.3.28", "container": "zjn-fluidckpt-n28", "gpus": [2, 3]},
    "node29": {"ip": "12.11.3.29", "container": "zjn-fluidckpt-n29", "gpus": [0, 1]},
}
PROFILE = {"rates": {"local_dram": 35, "local_hbm": 80, "peer": 35, "network": 20},
           "gpu_ingress_GBps": 80, "gpu_egress_GBps": 80, "dram_GBps": 50, "network_node_GBps": 30,
           "provenance": "conservative provisional capacities for functional planning, not calibrated cluster predictions"}
PROMPTS = ["The capital of France is", "Question: What is 2 + 3?\nAnswer:",
           "Write a Python function that adds two numbers.\n"]


class SystemRun:
    def __init__(self, args):
        self.args = args
        self.out = Path(getattr(args, "result_root", ROOT / "codex_coding/results/task2")) / args.run
        self.out.mkdir(parents=True, exist_ok=False)
        os.chmod(self.out, 0o777)
        self.processes = []
        self.owned_containers = []
        self.base, self.server_port = args.port_base, 31942
        self.members = [(node, gpu) for node, info in NODES.items() for gpu in info["gpus"]]
        self.endpoints = [f"http://{NODES[node]['ip']}:{self.base+gpu}" for node, gpu in self.members]
        self.cluster = Cluster(self.endpoints, PROFILE, planner_endpoint=self.endpoints[0])
        self.result = {"task": "task2", "development": getattr(args, "development", "v0.1.23.2"), "run": args.run, "status": "running",
                       "repeats": 1, "profile": PROFILE, "events": [], "stages": {}, "restores": [],
                       "scope": "fixed Qwen TP2 layout; DRAM/HBM sources on three nodes; no performance optimization claim"}
        files = list((ROOT / "main_lib/fluidckpt").rglob("*.py")) + list(Path(__file__).parent.glob("*.py"))
        for directory in getattr(args, "source_directories", []): files.extend(Path(directory).rglob("*.py"))
        files.append(ROOT / "codex_coding/src/serving/sglang_fluid_launch.py")
        self.result["code_sha256"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        self.env = {"PYTHONPATH": str(ROOT / "main_lib"), "HSA_FORCE_FINE_GRAIN_PCIE": "1",
                    "NCCL_NET_PLUGIN": "librccl-net-shca.so", "NCCL_DEBUG": "WARN", "AMD_SERIALIZE_KERNEL": "3",
                    "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "SGLANG_KV_LAYOUT_HCU_FA": "false"}

    def save(self):
        temporary = self.out / "summary.json.tmp"
        temporary.write_text(json.dumps(self.result, indent=2, ensure_ascii=False))
        temporary.replace(self.out / "summary.json")

    def event(self, event_name, **kw):
        row = {"time": time.time(), "event": event_name, **kw}
        self.result["events"].append(row); self.save()
        print(json.dumps(row, ensure_ascii=False), flush=True)

    def remote(self, node, command, timeout=120):
        script = "ulimit -c 0\nsource /opt/dtk/env.sh >/dev/null 2>&1\ncd " + shlex.quote(str(ROOT)) + "\nexec " + shlex.join(command)
        argv = ["docker", "exec", NODES[node]["container"], "bash", "-lc", script]
        if node != "node27": argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", node, shlex.join(argv)]
        return subprocess.check_output(argv, text=True, stderr=subprocess.STDOUT, timeout=timeout)

    def docker_host(self, node, command):
        argv = ["docker"]+command
        if node != "node27": argv = ["ssh", "-o", "BatchMode=yes", node, shlex.join(argv)]
        return subprocess.check_output(argv, text=True, stderr=subprocess.STDOUT, timeout=120)

    def isolated_containers(self):
        for node in (name for name in NODES if name != "node27"):
            existing = NODES[node]["container"]
            image = self.docker_host(node, ["inspect", existing, "--format", "{{.Config.Image}}"] ).strip()
            name = "zjn-fluidckpt-task2-"+self.args.run.replace("/", "-")
            command = ["run", "-d", "--name", name, "--label", "fluidckpt.session="+self.args.run,
                       "--network", "host", "--ipc", "host", "--cap-add", "SYS_NICE",
                       "--ulimit", "memlock=-1:-1", "--ulimit", "core=0:0"]
            for dev in ("/dev/kfd", "/dev/mkfd", "/dev/dri", "/dev/infiniband"):
                command += ["--device", dev]
            for mount in ("/opt/hyhal:/opt/hyhal:ro", "/home/zhujianian:/home/zhujianian",
                          "/data/node28/shared/zhujianian:/data/node28/shared/zhujianian:ro"):
                command += ["-v", mount]
            command += [image, "sleep", "infinity"]
            self.docker_host(node, command)
            self.owned_containers.append((node, name))
            NODES[node]["container"] = name
            self.event("isolated_container_created", node=node, name=name, capability="SYS_NICE")

    def start(self, node, name, command, env=None):
        path = self.out / f"{name}.process.json"
        path.write_text(json.dumps({"command": command, "env": dict(self.env, **(env or {})),
                                   "cwd": str(ROOT), "log": str(self.out / f"{name}.log")}))
        self.processes.append((node, path))
        text = self.remote(node, ["python3", "codex_coding/src/task2/cluster/process.py", "start", "--spec", str(path)])
        self.event("process_started", node=node, name=name, process=json.loads(text))
        return node, path

    def stop(self, handle):
        node, path = handle
        self.remote(node, ["python3", "codex_coding/src/task2/cluster/process.py", "stop", "--spec", str(path)], timeout=45)
        self.processes.remove(handle)

    def wait_http(self, endpoint, operation="status", timeout=120):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            try: return request(endpoint, operation, timeout=2)
            except Exception: time.sleep(0.5)
        raise TimeoutError(f"{endpoint}/{operation} not ready")

    def preflight(self):
        def inspect(node):
            out = self.out / f"{node}_topology.json"
            self.remote(node, ["python3", "codex_coding/src/task2/cluster/inspect_node.py", "--out", str(out)])
            return node, json.loads(out.read_text())
        with ThreadPoolExecutor(3) as pool: probes = dict(pool.map(inspect, NODES))
        for node, info in NODES.items():
            needed_ports = {self.base+g for g in info["gpus"]} | {self.server_port}
            if node == "node27": needed_ports.add(self.base+100)
            if needed_ports.intersection(probes[node].get("occupied_tcp_ports", probes[node]["listening_ports"])):
                raise RuntimeError(f"{node}: experiment port already in use")
            for gpu in info["gpus"]:
                if int(probes[node]["gpu_memory"][f"card{gpu}"]["vram Total Used Memory (MiB)"]) > 1024:
                    raise RuntimeError(f"{node}:{gpu} is busy")
        self.probes = probes
        self.result["preflight"] = probes
        self.event("preflight_passed", nodes=list(NODES))

    def start_workers(self):
        mesh = getattr(self.args, "mesh", "node27:0-3,node28:2-3,node29:0-1")
        def launch(member):
            node, gpu = member
            near = next(g["numa_node"] for g in self.probes[node]["topology"]["gpus"] if g["logical_gpu"] == gpu)
            near = getattr(self.args, "worker_numas", {}).get(f"{node}:{gpu}", near)
            near = getattr(self.args, "worker_numa", {}).get(f"{node}:{gpu}", near)
            return self.start(node, f"worker_{node}_{gpu}", ["python3", "-m", "fluidckpt.cluster.worker",
                "--node", node, "--gpu", str(gpu), "--numa", str(near), "--port-base", str(self.base),
                "--mesh", mesh, "--master", f"12.11.3.27:{self.base+100}", "--handoff", str(self.out / "handoff"),
                "--session", self.args.run], {"AMD_SERIALIZE_KERNEL": str(self.args.worker_serialize), "NCCL_DEBUG": "INFO"})
        # Process specs and the summary are mutated sequentially; mesh initialization is collective.
        for member in self.members: launch(member)
        for endpoint in self.endpoints: self.wait_http(endpoint)
        self.cluster.call_all("mesh", [{} for _ in self.members])
        workers, _ = self.cluster.inventory()
        self.result["stages"]["1_resource_directory"] = {"passed": True, "workers": workers}
        self.event("mesh_ready", workers=len(workers))

    def adopt(self, snap, prefix):
        return [request(self.endpoints[g], "adopt", {"snap": snap, "tp": g, "id": f"{prefix}/{g}"}) for g in range(2)]

    def region_request(self, source, rid, worker, tier, off=0, n=None):
        return {"id": rid, "identity": source["identity"], "tp": source["tp"], "off": off,
                "n": source["n"] if n is None else n, "preferred_worker": worker,
                "locations": [{"worker": worker, "tier": tier}]}

    def replica_target(self, source, rid, worker, tier="hbm", off=0, n=None):
        return {"key": rid, "id": rid, "mode": "replica", "worker": worker, "tier": tier,
                "identity": source["identity"], "tp": source["tp"], "off": off,
                "n": source["n"] if n is None else n, "meta": source["meta"], "index": source["index"]}

    def fixture(self):
        size = (64 << 20)+17
        for g in range(2): request(self.endpoints[g], "synth", {"snap": "fixture", "tp": g, "nbytes": size, "seed": 2026+g})
        sources = self.adopt("fixture", "fixture/base")
        rejects = {}
        try: self.cluster.retire(sources[0]["id"])
        except ValueError: rejects["last_replica"] = True
        reqs = [self.region_request(s, f"fixture/gpu/{g}", g+2, "hbm", 17, (32 << 20)+3) for g, s in enumerate(sources)]
        gpu = self.cluster.place(reqs, extra_budget=128 << 20)
        peer_targets = [self.replica_target(s, f"fixture/peer/{g}", g, off=17, n=(32 << 20)+3) for g, s in enumerate(sources)]
        peer = self.cluster.transfer(self.cluster.plan_restore(peer_targets, source_ids=[f"fixture/gpu/{g}" for g in range(2)]))
        self.result["stages"]["4_gpu_source"] = {"passed": True, "build": gpu, "peer_restore": peer}
        moves = [self.cluster.migrate(s["id"], f"fixture/remote/{g}", g+4, "dram") for g, s in enumerate(sources)]
        self.result["stages"]["2_automatic_placement"] = {"passed": True, "moves": moves}
        targets = [self.replica_target(s, f"fixture/target/{g}", g+6) for g, s in enumerate(sources)]
        broken = self.cluster.plan_restore(targets)
        broken["ops"][0]["generation"] += 1
        try: self.cluster.transfer(broken)
        except RuntimeError: rejects["stale_generation"] = True
        try: self.cluster.place([self.region_request(sources[0], "overbudget", 6, "dram")], extra_budget=1)
        except RuntimeError: rejects["capacity"] = True
        self.result["stages"]["3_lifecycle"] = {"passed": len(rejects) == 3, "rejections": rejects}
        if len(rejects) != 3: raise AssertionError("missing expected rejection")
        candidates = [self.region_request(s, f"fixture/joint/{g}", g+6, "dram", 17, 16 << 20) for g, s in enumerate(sources)]
        for candidate in candidates: candidate["locations"].append({"worker": candidate["tp"]+4, "tier": "dram"})
        joint = self.cluster.place(candidates, "joint_greedy", [{"targets": targets}], extra_budget=32 << 20)
        if not joint["placement"]["decisions"]: raise AssertionError("joint planner did not choose useful local replicas")
        multi = self.cluster.transfer(self.cluster.plan_restore(targets, "balanced"))
        lp_targets = [dict(t, key=t["key"]+"lp", id=t["id"]+"lp") for t in targets]
        lp = self.cluster.transfer(self.cluster.plan_restore(lp_targets, "resource_lp",
            source_ids=[f"fixture/remote/{g}" for g in range(2)]+[f"fixture/gpu/{g}" for g in range(2)]))
        self.result["stages"]["6_multisource_fixture"] = {"passed": True, "balanced": multi, "resource_lp": lp}
        self.result["stages"]["7_joint_placement"] = {"passed": True, "fixture": joint}
        workers, replicas = self.cluster.inventory()
        self.result["fixture_final_inventory"] = workers
        # Explicitly delete the finished fixture model; this is distinct from evicting live coverage.
        for r in replicas: request(self.endpoints[r["worker"]], "retire", {"id": r["id"]})
        workers, replicas = self.cluster.inventory()
        if replicas or any(any(w["used_bytes"].values()) or w["transactions"] for w in workers):
            raise AssertionError("fixture allocations or leases remain")
        self.event("fixture_passed", stages=list(self.result["stages"]))

    def server(self, node, phase, mode):
        argv = ["python3", "codex_coding/src/serving/sglang_fluid_launch.py", "--model-path", self.args.model,
                "--host", "0.0.0.0", "--port", str(self.server_port), "--tp-size", "2", "--mem-fraction-static", "0.70",
                "--context-length", "4096", "--max-running-requests", "4", "--disable-cuda-graph", "--attention-backend", "triton",
                "--page-size", "1", "--moe-runner-backend", "triton", "--max-total-tokens", "8192", "--disable-custom-all-reduce",
                "--mamba-scheduler-strategy", "extra_buffer", "--max-mamba-cache-size", "32", "--random-seed", "42"]
        if mode == "restore": argv += ["--load-format", "dummy"]
        env = {"HIP_VISIBLE_DEVICES": "0,1", "FLUIDCKPT_MODE": mode, "FLUIDCKPT_TAG": phase,
               "FLUIDCKPT_HANDOFF": str(self.out / "handoff"), "FLUIDCKPT_METRICS": str(self.out / "metrics" / phase),
               "FLUIDCKPT_CAPTURE_WAIT": "1", "FLUIDCKPT_TIMEOUT": "600", "FLUIDCKPT_MODEL_ID": self.model_identity,
               "FLUIDCKPT_TP_SIZE": "2", "FLUIDCKPT_MANIFEST": str(self.out / "handoff/capture/derived.json")}
        return self.start(node, phase, argv, env)

    def wait_exports(self, phase):
        return wait_for_exports(self.out / "handoff" / phase,
                                poll_s=getattr(self.args, "handoff_poll", 0.01))

    def generate(self, node):
        endpoint = f"http://{NODES[node]['ip']}:{self.server_port}"
        self.wait_http(endpoint, "health", timeout=600)
        replies = [request(endpoint, "generate", {"text": prompt,
            "sampling_params": {"temperature": 0, "max_new_tokens": 32},
            "return_logprob": True, "logprob_start_len": -1}, timeout=180) for prompt in PROMPTS]
        return replies

    def prepare_model(self):
        digest = hashlib.sha256()
        model = Path(self.args.model)
        for name in ("config.json", "model.safetensors.index.json"): digest.update((model / name).read_bytes())
        for path in sorted(model.glob("*.safetensors")):
            digest.update(f"{path.name}:{path.stat().st_size}:{path.stat().st_mtime_ns}".encode())
        self.model_identity = digest.hexdigest()
        server = self.server("node27", "capture", "capture")
        self.wait_exports("capture")
        with ThreadPoolExecutor(2) as pool:
            captures = list(pool.map(lambda g: request(self.endpoints[g], "capture", {"tag": "capture", "snap": "model"}), range(2)))
        reference = self.generate("node27")
        checks = [request(self.endpoints[g], "verify_capture", {"tag": "capture", "snap": "model", "tp": g}) for g in range(2)]
        if not all(c["passed"] for c in checks): raise AssertionError("capture differs from running model")
        for g in range(2): request(self.endpoints[g], "release_live", {"tag": "capture"})
        self.stop(server)
        sources = self.adopt("model", "model/base")
        self.result["capture"] = {"workers": captures, "verification": checks, "replies": reference, "sources": sources}
        self.event("model_captured", bytes=sum(s["n"] for s in sources))
        self.result["model_gpu_placement"] = self.cluster.place([
            self.region_request(s, f"model/gpu/{g}", getattr(self.args, "replica_workers", [2, 3])[g], "hbm", (1 << 30)+17, 4 << 30) for g, s in enumerate(sources)], extra_budget=8 << 30)
        self.event("model_gpu_replicas_ready")
        self.result["model_migrations"] = [self.cluster.migrate(s["id"], f"model/remote/{g}", getattr(self.args, "source_workers", [4, 5])[g], "dram") for g, s in enumerate(sources)]
        self.event("model_base_ready" if hasattr(self.args, "source_workers") else "model_migrated_to_node28")
        return sources, reference

    def real_model(self):
        sources, reference = self.prepare_model()
        self.restore_model("node27", "two_node_restore", sources, [0, 1], "nearest", [f"model/remote/{g}" for g in range(2)], reference)
        self.result["stages"]["5_two_node_model"] = {"passed": True, "phase": "two_node_restore"}
        future = [self.replica_target(s, f"future/{g}", g+6) for g, s in enumerate(sources)]
        candidates = [self.region_request(s, f"model/joint/{g}", g+6, "dram", (1 << 30)+17, 1 << 30) for g, s in enumerate(sources)]
        for candidate in candidates: candidate["locations"].append({"worker": candidate["tp"]+4, "tier": "dram"})
        self.result["model_joint_placement"] = self.cluster.place(candidates, "joint_greedy", [{"targets": future}], extra_budget=2 << 30)
        self.event("model_joint_placement_ready", decisions=len(self.result["model_joint_placement"]["placement"]["decisions"]))
        self.restore_model("node29", "three_node_restore", sources, [6, 7], "balanced", None, reference)
        self.result["stages"]["8_three_node_model"] = {"passed": True, "phase": "three_node_restore"}

    def restore_model(self, node, phase, sources, dsts, strategy, source_ids, reference):
        server = self.server(node, phase, "restore")
        self.wait_exports(phase)
        targets = [dict(self.replica_target(s, f"{phase}/{g}", dsts[g]), mode="live", tag=phase) for g, s in enumerate(sources)]
        for target in targets: target["metrics_directory"] = str(self.out / "metrics" / phase)
        started = time.monotonic()
        plan = self.cluster.plan_restore(targets, strategy, source_ids)
        planned = time.monotonic()
        flow = self.cluster.transfer(plan, verify_before_ready=False, retain=True)
        self.event("weights_transferred", phase=phase, control_s=time.monotonic()-started, source_bytes=flow["source_bytes"])
        replies = self.generate(node)
        if [r["text"] for r in replies] != [r["text"] for r in reference]:
            raise AssertionError("restored model generation differs from captured model")
        verify = self.cluster.verify(flow["tx"])
        timing = summarize(self.out / "metrics" / phase, 2)
        self.cluster.finish(flow["tx"])
        self.stop(server)
        self.result["restores"].append({"phase": phase, "node": node, "strategy": strategy, "flow": flow,
            "verification": verify, "replies": replies, "timing": timing, "passed": True,
            "plan_wall_s": planned-started})
        self.event("model_restore_passed", phase=phase, load_s=timing["load_s"], verified_bytes=verify["checked_bytes"])

    def run(self):
        try:
            self.isolated_containers(); self.preflight(); self.start_workers(); self.fixture()
            if not self.args.fixture_only: self.real_model()
            self.result["final_inventory"] = self.cluster.inventory()[0]
            mismatches = [name for name, sha in self.result["code_sha256"].items() if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != sha]
            self.result["source_hash_mismatches"] = mismatches
            if mismatches: raise AssertionError(f"measured code changed: {mismatches}")
            self.result["status"] = "passed"
        except BaseException as exc:
            self.result.update(status="failed", error=repr(exc))
            self.event("failed", error=repr(exc))
            raise
        finally:
            failures = []
            for handle in list(reversed(self.processes)):
                try: self.stop(handle)
                except Exception as exc: failures.append(str(exc))
            for node, name in self.owned_containers:
                try: self.docker_host(node, ["rm", "-f", name])
                except Exception as exc: failures.append(str(exc))
            self.result["cleanup_errors"] = failures
            self.save()
            print(json.dumps({"status": self.result["status"], "cleanup_errors": failures}), flush=True)


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(); parser.add_argument("--run", required=True)
    parser.add_argument("--fixture-only", action="store_true")
    parser.add_argument("--port-base", type=int, default=46100)
    parser.add_argument("--worker-serialize", type=int, default=0)
    parser.add_argument("--handoff-poll", type=float, default=0.01)
    parser.add_argument("--model", default="/data/node28/shared/zhujianian/Qwen3.6-35B-A3B")
    SystemRun(parser.parse_args()).run()
