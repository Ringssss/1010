"""Fresh exact PID/group evidence after the existing supervisor's STOP cleanup."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--boot", required=True)
    args = parser.parse_args()
    helper = Path("/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/src/v0.1.23.3/task2/three_node_recovery/admission28/runtime_gate_r03.py")
    if hashlib.sha256(helper.read_bytes()).hexdigest() != "d1ae5e31c3bf801d36011cbb9a6e911b400fd4e64c6689c8c742d4aa78d4f058":
        raise ValueError("frozen metadata helper SHA differs")
    module_spec = importlib.util.spec_from_file_location("exact_proc_metadata", helper)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    namespace = os.readlink("/proc/self/ns/pid")
    if namespace != args.namespace or module.boot_id() != args.boot:
        raise ValueError("process namespace or boot differs")
    process = module.proc(args.pid)
    group = module.group_members(args.pid)
    passed = process.get("state") == "absent" and not group
    print(json.dumps({"passed": passed, "expected_pid": args.pid, "expected_start_ticks": args.start,
        "namespace": namespace, "boot_id": args.boot, "actual": process, "group_members": group,
        "signals": [], "GPU_calls": 0}, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
