"""Exercise exact observer identity and bounded proc reads on one new CPU child."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import observe_node28_init as observer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.fixture:
        time.sleep(0.5)
        return
    args.out.mkdir(parents=True, exist_ok=False)
    argv = [sys.executable, str(Path(__file__).resolve()), "--fixture"]
    child = subprocess.Popen(argv, start_new_session=True)
    while observer.proc(child.pid).get("argv") != argv:
        if child.poll() is not None:
            raise RuntimeError("CPU child exited before ownership registration")
        time.sleep(0.005)
    observer.BOOT = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    observer.PIDNS = os.readlink("/proc/self/ns/pid")
    observer.EXPECTED_ARGV = argv
    owner = observer.proc(child.pid)
    owner.update(boot_id=observer.BOOT, pid_namespace=observer.PIDNS)
    sample = observer.snapshot(owner, 0, time.monotonic_ns())
    bad = dict(owner, start_time=str(int(owner["start_time"])+1))
    rejected = observer.snapshot(bad, 0, time.monotonic_ns())
    child.wait(timeout=2)
    absent = observer.snapshot(owner, 0, time.monotonic_ns())
    checks = {"live_exact": sample["exact_before"] and sample["exact_after"],
              "limits": len(sample["threads"]) <= 32 and len(sample["fds"]) <= 16,
              "wrong_start_rejected_without_details": not rejected["exact_before"] and "threads" not in rejected and "fds" not in rejected,
              "exit_rejected_without_details": not absent["exact_before"] and "threads" not in absent,
              "missing_read_explicit": "error" in observer.read("/proc/0/stat")}
    result = {"passed": all(checks.values()), "checks": checks, "sample": sample, "wrong_start": rejected,
              "after_exit": absent, "signals": 0, "gpu_operations": 0, "ssh_calls": 0}
    (args.out/"summary.json").write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps({"passed": result["passed"], "checks": checks}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
