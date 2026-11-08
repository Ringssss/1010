"""Launch one bounded tiny hash probe on node27 and clean only its owned container."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.benchmarks.serverless_adapter import atomic_json


def no_core():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main():
    no_core()
    p = argparse.ArgumentParser()
    p.add_argument("--case", choices=["current", "set_device"], required=True)
    p.add_argument("--run", required=True)
    p.add_argument("--timeout", type=float, default=100)
    a = p.parse_args()
    if not a.run.startswith("serverless_") or "/" in a.run:
        raise ValueError("use a new serverless_* run")
    out = ROOT / "codex_coding/results/v0.1.23.3/task2/baseline_loading" / a.run
    out.mkdir(parents=True, exist_ok=False)
    name = "zjn-" + a.run.replace("_", "-")
    result = {"status": "running", "case": a.case, "container": name, "commands": []}
    created = False
    worker = None
    def command(argv):
        value = subprocess.run(argv, capture_output=True, text=True, timeout=30, preexec_fn=no_core)
        result["commands"].append({"argv": argv, "returncode": value.returncode, "stdout": value.stdout, "stderr": value.stderr})
        atomic_json(out / "summary.json", result)
        if value.returncode:
            raise RuntimeError("probe container command failed")
        return value.stdout.strip()
    try:
        image = command(["docker", "inspect", "zjn-fluid-basic-1bce3897a2075b", "--format", "{{.Image}}"])
        docker = ["docker", "run", "-d", "--name", name, "--label", "fluidckpt.serverless_probe="+a.run,
            "--ipc", "host", "--network", "host", "--memory", "8g", "--cpus", "4", "--ulimit", "core=0:0",
            "--ulimit", "memlock=-1:-1", "--security-opt", "seccomp=unconfined", "-v", f"{ROOT}:{ROOT}",
            "-v", "/opt/hyhal:/opt/hyhal:ro"]
        for device in ("/dev/kfd", "/dev/mkfd", "/dev/dri"):
            if Path(device).exists():
                docker.extend(["--device", device])
        command(docker + [image, "sleep", "infinity"])
        created = True
        argv = ["docker", "exec", name, "env", "PYTHONPATH="+str(ROOT / "main_lib"), "HIP_VISIBLE_DEVICES=0,1",
            "HSA_FORCE_FINE_GRAIN_PCIE=1", "LD_LIBRARY_PATH=/opt/dtk/dcc/gcvm/lib:/opt/dtk/llvm/lib:/opt/dtk/lib:/opt/dtk/hip/lib:/opt/hyhal/lib:/opt/hyhal/lib64:/opt/dtk/.hyhal/lib:/opt/dtk/.hyhal/lib64:/opt/dtk/.hyhal/rocm_smi/lib",
            "python", str(Path(__file__).parent / "hash_probe.py"), "--case", a.case,
            "--source-checkout", str(ROOT / "lib_cite/ServerlessLLM-hip"), "--out", str(out / "worker")]
        result["worker_argv"] = argv
        with (out / "worker.log").open("w") as log:
            worker = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, preexec_fn=no_core)
        code = worker.wait(timeout=a.timeout)
        result["worker_exit_code"] = code
        result["status"] = "passed" if code == 0 else "failed"
        if (out / "worker/result.json").exists():
            result["worker_result"] = json.loads((out / "worker/result.json").read_text())
    except Exception as error:
        result["status"] = "failed"
        result["error"] = repr(error)
    finally:
        if created:
            try:
                command(["docker", "rm", "-f", name])
                result["container_removed"] = True
                if worker is not None and worker.poll() is None:
                    worker.wait(timeout=10)
            except Exception as error:
                result["cleanup_error"] = repr(error)
        atomic_json(out / "summary.json", result)
    print(json.dumps({"status": result["status"], "summary": str(out / "summary.json")}))


if __name__ == "__main__":
    main()
