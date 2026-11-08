"""Bounded exact-owner cleanup for xml_startup_owned_r02, never start work."""
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
import urllib.request

ROOT = Path(__file__).resolve().parents[6]
RUN = ROOT / "codex_coding/results/v0.1.23.3/task2/resource_map/xml_startup_owned_r02"
STOP = ROOT / "codex_coding/src/task2/cluster/process.py"
EXPECTED = {2: (21883, "475875814"), 3: (21901, "475875846")}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def proc(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()
        return {"pid": pid, "exists": True, "state": fields[0], "live": fields[0] != "Z",
                "start_time": fields[19], "pgid": int(fields[2]),
                "argv": Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")[:-1]}
    except FileNotFoundError:
        return {"pid": pid, "exists": False, "live": False}


def worker(args):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    frozen = json.loads(args.expected)
    if sha(STOP) != frozen["stop_sha"]:
        raise ValueError("original stop tool SHA mismatch")
    result = {"owned_run": str(RUN), "signals_limited_to_owned_groups": True, "ranks": [], "protected": []}
    for rank, (pid, stamp) in EXPECTED.items():
        spec_path = RUN / f"rank_{rank}.process.json"
        state_path = spec_path.with_suffix(".pid.json")
        if sha(spec_path) != frozen["specs"][str(rank)]["spec_sha"] or sha(state_path) != frozen["specs"][str(rank)]["state_sha"]:
            raise ValueError("remote spec/state SHA mismatch")
        spec, recorded = json.loads(spec_path.read_text()), json.loads(state_path.read_text())
        if recorded != {"pid": pid, "start_time": stamp, "spec": str(spec_path)}:
            raise ValueError("recorded owner mismatch")
        if spec["owner_node"] != "node29" or spec["owner_rank"] != rank or spec["ownership_namespace"] != RUN.name:
            raise ValueError("spec namespace mismatch")
        before = proc(pid)
        if before["exists"] and before["start_time"] != stamp:
            raise ValueError("PID reused; refusing stop")
        if before["live"]:
            if before["pgid"] != pid or before["argv"] != spec["command"]:
                raise ValueError("process group/argv mismatch; refusing stop")
        row = {"rank": rank, "recorded": recorded, "before": before}
        try:
            command = ["python3", str(STOP), "stop", "--spec", str(spec_path)]
            stopped = subprocess.run(command, capture_output=True, text=True, timeout=25)
            row.update(stop_argv=command, stop_returncode=stopped.returncode,
                       stop_stdout=stopped.stdout, stop_stderr=stopped.stderr)
        except subprocess.TimeoutExpired as exc:
            row.update(stop_returncode=None, stop_timeout=True, stop_stdout=exc.stdout, stop_stderr=exc.stderr)
        row["after"] = proc(pid)
        row["passed"] = not row["after"]["live"] and row.get("stop_returncode") == 0
        result["ranks"].append(row)
    for path in frozen["protected_specs"]:
        registered = json.loads(Path(path).with_suffix(".pid.json").read_text())
        actual = proc(registered["pid"])
        result["protected"].append({"spec": path, "registered": registered, "actual": actual,
                                    "passed": actual["live"] and actual["start_time"] == registered["start_time"]})
    result["exact_cleanup_passed"] = all(row["passed"] for row in result["ranks"])
    print(json.dumps(result))


def http(endpoint):
    begin = time.monotonic()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(endpoint + "/inventory?compact=1", timeout=4) as response:
            raw = response.read()
        payload = json.loads(raw)
        return {"endpoint": endpoint, "passed": True, "elapsed_s": time.monotonic()-begin,
                "response_sha256": hashlib.sha256(raw).hexdigest(), "worker": payload.get("worker"),
                "session": payload.get("session"), "transactions": payload.get("transactions"),
                "replica_count": len(payload.get("replicas", []))}
    except Exception as exc:
        return {"endpoint": endpoint, "passed": False, "elapsed_s": time.monotonic()-begin, "error": repr(exc)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--expected")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    allowed = ROOT / "codex_coding/results/v0.1.23.3/task2/resource_map"
    if not args.out or args.out.resolve().parent != allowed or not args.out.name.startswith("xml_recovery_"):
        parser.error("require new resource_map/xml_recovery_* result")
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    source_summary_sha = sha(RUN / "summary.json")
    state = json.loads((ROOT / "codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json").read_text())
    protected = {path for field in ("basic_owned_processes", "recovery_owned_processes", "new_engine_processes")
                 for node,path in state.get(field, []) if node == "node29"}
    frozen = {"stop_sha": sha(STOP), "specs": {}, "protected_specs": sorted(protected)}
    for rank, (pid, stamp) in EXPECTED.items():
        path = RUN / f"rank_{rank}.process.json"
        recorded = json.loads(path.with_suffix(".pid.json").read_text())
        if recorded != {"pid": pid, "start_time": stamp, "spec": str(path)}:
            raise ValueError("local exact owner mismatch")
        frozen["specs"][str(rank)] = {"spec_sha": sha(path), "state_sha": sha(path.with_suffix(".pid.json"))}
    summary = {"owned_run": str(RUN), "execute": args.execute, "gpu_workloads": 0,
               "source_summary_sha": source_summary_sha, "exact_targets": frozen, "ssh_attempts": [],
               "script_sha256": sha(__file__), "http": [], "icmp": [], "exact_remote_cleanup_passed": False}
    (args.out / "source.py").write_bytes(Path(__file__).read_bytes())
    if args.execute:
        with ThreadPoolExecutor(max_workers=4) as executor:
            http_results = list(executor.map(http, ("http://12.11.3.29:33000", "http://12.11.3.29:33001")))
        summary["http"] = http_results
        for address in ("10.211.3.29", "12.11.3.29"):
            argv = ["ping", "-n", "-c", "1", "-W", "2", address]
            result = subprocess.run(argv, capture_output=True, text=True, timeout=4)
            summary["icmp"].append({"argv": argv, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        remote_command = ["docker", "exec", "-i", "zjn-fluid-basic-1bce3897a2075b", "python3", "-", "--worker", "--expected", json.dumps(frozen)]
        for path in ("node29", "12.11.3.29"):
            argv = ["ssh"] + (["-F", "/dev/null"] if path != "node29" else []) + ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ConnectionAttempts=1", "-o", "ServerAliveInterval=3", "-o", "ServerAliveCountMax=1", path, shlex.join(remote_command)]
            begin = time.monotonic()
            try:
                result = subprocess.run(argv, input=Path(__file__).read_bytes(), capture_output=True, timeout=60)
                record = {"path": path, "argv": argv, "returncode": result.returncode, "elapsed_s": time.monotonic()-begin}
                (args.out / (path.replace(".", "_") + ".stdout")).write_bytes(result.stdout)
                (args.out / (path.replace(".", "_") + ".stderr")).write_bytes(result.stderr)
                if result.returncode == 0:
                    record["remote"] = json.loads(result.stdout)
                    summary["exact_remote_cleanup_passed"] = record["remote"]["exact_cleanup_passed"]
            except subprocess.TimeoutExpired as exc:
                record = {"path": path, "argv": argv, "returncode": None, "elapsed_s": time.monotonic()-begin, "timeout": True}
                (args.out / (path.replace(".", "_") + ".stdout")).write_bytes(exc.stdout or b"")
                (args.out / (path.replace(".", "_") + ".stderr")).write_bytes(exc.stderr or b"")
            summary["ssh_attempts"].append(record)
            if summary["exact_remote_cleanup_passed"]:
                break
    summary["original_summary_unchanged"] = sha(RUN / "summary.json") == source_summary_sha
    summary["pending_exact_targets"] = [] if summary["exact_remote_cleanup_passed"] else [{"rank": r,"pid":p,"start_time":s,"node":"node29"} for r,(p,s) in EXPECTED.items()]
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2)+"\n")
    print(json.dumps({"out":str(args.out), "exact_remote_cleanup_passed":summary["exact_remote_cleanup_passed"], "pending":summary["pending_exact_targets"]}))


if __name__ == "__main__":
    main()
