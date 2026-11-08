"""Bounded read-only timing/stack diagnosis of the unchanged snapshot path."""
import argparse
import faulthandler
import hashlib
import importlib
import json
import os
from pathlib import Path
import resource
import runpy
import shlex
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[6]
HERE = Path(__file__).resolve().parent


def worker(args):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    faulthandler.enable(file=sys.stderr, all_threads=True)
    faulthandler.dump_traceback_later(15, repeat=True, file=sys.stderr)
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.alarm(80)
    start = time.monotonic()
    def event(stage, **data):
        print(json.dumps({"elapsed_s": round(time.monotonic()-start, 6), "stage": stage,
                          "pid": os.getpid(), **data}), file=sys.stderr, flush=True)
    event("worker_begin", gpu_workloads=0, gpu_allocations=0, communication_initializations=0)
    sys.path.insert(0, str(ROOT / "main_lib"))
    sys.path.insert(0, str(ROOT / "codex_coding/src/v0.1.23.3/task2/topology_alignment/probe"))
    event("imports_begin")
    import read_evidence
    discovery = importlib.import_module("fluidckpt.topology.discover")
    base = read_evidence.previous.base
    diagnosis = read_evidence.previous
    event("imports_end")
    original_output = subprocess.check_output
    def output(argv, *positional, **kwargs):
        event("subprocess_begin", argv=argv)
        begin = time.monotonic()
        kwargs.setdefault("timeout", 60)
        try:
            return original_output(argv, *positional, **kwargs)
        finally:
            event("subprocess_end", argv=argv, duration_s=time.monotonic()-begin)
    subprocess.check_output = output
    files = {str(Path(discovery.__file__).resolve()), str(Path(base.__file__).resolve()),
             str(Path(diagnosis.__file__).resolve())}
    functions = {"discover", "node_snapshot", "facts", "counters", "protected_specs"}
    def trace(frame, kind, arg):
        if frame.f_code.co_filename in files and frame.f_code.co_name in functions:
            if kind in ("call", "line", "return", "exception"):
                event("python_" + kind, function=frame.f_code.co_name,
                      filename=frame.f_code.co_filename, line=frame.f_lineno)
            return trace
        return None
    sys.settrace(trace)
    sys.argv = [str(HERE / "read_owned_evidence.py"), "--session", str(args.session.resolve()),
                "--node-snapshot", args.worker, "--hca", "shca_0", "--ifname", "ib0"]
    try:
        runpy.run_path(str(HERE / "read_owned_evidence.py"), run_name="__main__")
        event("worker_passed")
    finally:
        sys.settrace(None)
        faulthandler.cancel_dump_traceback_later()
        signal.alarm(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--worker", choices=("node27", "node29"))
    parser.add_argument("--node", choices=("node27", "node29"), default="node27")
    parser.add_argument("--container", default="zjn-fluid-basic-1bce3897a2075b")
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    if args.out is None:
        parser.error("--out required")
    allowed = ROOT / "codex_coding/results/v0.1.23.3/task2/resource_map"
    args.out = args.out.resolve()
    if args.out.parent != allowed or not args.out.name.startswith("xml_inventory_diagnosis_"):
        parser.error("require fresh owned diagnosis result")
    args.out.mkdir(parents=True, exist_ok=False)
    shell = "\n".join(["ulimit -c 0", "source /opt/dtk/env.sh >/dev/null 2>&1",
        "cd " + shlex.quote(str(ROOT)), "exec timeout --kill-after=2s 85s " + shlex.join([
        "python3", str(Path(__file__).resolve()), "--worker", args.node, "--session", str(args.session.resolve())])])
    argv = ["docker", "exec", args.container, "bash", "-lc", shell]
    if args.node == "node29":
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "12.11.3.29", shlex.join(argv)]
    begin = time.monotonic()
    summary = {"node": args.node, "argv": argv, "read_only": True, "gpu_workloads": 0,
               "gpu_allocations": 0, "communication_initializations": 0, "outer_timeout_s": 90,
               "inner_alarm_s": 80, "stack_interval_s": 15,
               "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    try:
        process = subprocess.run(argv, capture_output=True, timeout=90)
        stdout, stderr = process.stdout, process.stderr
        summary["returncode"] = process.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = exc.stdout or b"", exc.stderr or b""
        summary.update(returncode=None, outer_timeout=True)
    summary["elapsed_s"] = time.monotonic()-begin
    (args.out / "stdout.json").write_bytes(stdout)
    (args.out / "stages_and_stacks.log").write_bytes(stderr)
    (args.out / "source.py").write_bytes(Path(__file__).read_bytes())
    summary["files"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.out.iterdir() if p.is_file()}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2)+"\n")
    print(json.dumps({k: summary.get(k) for k in ("node", "returncode", "elapsed_s", "outer_timeout")}))


if __name__ == "__main__":
    main()
