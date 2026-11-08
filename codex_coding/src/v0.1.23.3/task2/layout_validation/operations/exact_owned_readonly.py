"""Reuse frozen proc parsing to check only the new worker's exact owner and group."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path("/home/zhujianian/zhujianian/fluidckpt_zjn")
HELPER = ROOT / "codex_coding/src/v0.1.23.3/task2/three_node_recovery/admission28/runtime_gate_r03.py"
HELPER_SHA = "d1ae5e31c3bf801d36011cbb9a6e911b400fd4e64c6689c8c742d4aa78d4f058"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", type=Path, required=True)
    args = parser.parse_args()
    if hashlib.sha256(HELPER.read_bytes()).hexdigest() != HELPER_SHA:
        raise ValueError("frozen proc helper changed")
    module_spec = importlib.util.spec_from_file_location("frozen_proc_reader", HELPER)
    gate = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(gate)
    raw = args.owner.read_bytes()
    owner = json.loads(raw)
    boot, namespace = gate.boot_id(), os.readlink("/proc/self/ns/pid")
    if boot != owner["boot_id"] or namespace != owner["pid_namespace"]:
        raise ValueError("owner boot or PID namespace changed; no old process lookup")
    current = gate.proc(owner["pid"])
    members = gate.group_members(owner["pgid"])
    passed = current.get("state") == "absent" and not members
    print(json.dumps({"boot_id": boot, "pid_namespace": namespace, "owner": owner,
          "owner_sha256": hashlib.sha256(raw).hexdigest(), "current": current,
          "group_members": members, "passed": passed, "signals": 0, "GPU_calls": 0}, indent=2))
    if not passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
