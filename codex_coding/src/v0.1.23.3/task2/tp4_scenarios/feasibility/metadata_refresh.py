"""Read metadata through the installed frozen guard without keeper RPC or signals."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", choices=("node28", "node30"), required=True)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    bootstrap = Path("/tmp/fluidckpt_minimal_calibration_r01_bootstrap")
    spec = json.loads((bootstrap / (args.node + ".integration.guard.spec.json")).read_text())
    source = bootstrap / "measurement_host.source.py"
    actual_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual_sha != spec["host_wrapper_sha256"]:
        raise ValueError("installed guard source SHA differs")
    if os.uname().nodename != spec["hostname"] or Path("/proc/sys/kernel/random/boot_id").read_text().strip() != spec["boot_id"]:
        raise ValueError("host identity differs")
    module_spec = importlib.util.spec_from_file_location("frozen_metadata_guard", source)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)

    def command_without_signals(argv, timeout=5):
        child = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, stderr = child.communicate(timeout=timeout)
            return {"argv": argv, "returncode": child.returncode,
                    "stdout": stdout.decode(errors="replace"), "stderr": stderr.decode(errors="replace")}
        except subprocess.TimeoutExpired:
            return {"argv": argv, "returncode": None, "timed_out": True,
                    "pending_cli_pid": child.pid, "stdout": "", "stderr": "", "signals": []}

    module.command = command_without_signals
    spec["keeper_endpoints"] = []
    result = module.guard(spec)
    result["container_identity_checks"] = {
        "id": result["container"].get("id") == spec.get("expected_container_id"),
        "started": result["container"].get("State", {}).get("StartedAt") == spec.get("expected_container_started"),
        "running": result["container"].get("State", {}).get("Running") is True,
    }
    result.update(sampled_at_utc=datetime.now(timezone.utc).isoformat(),
                  frozen_guard_sha256=actual_sha, keeper_rpc_calls=0, keeper_catalog_refreshed=False,
                  signals=[], remote_writes=0, gpu_calls=0,
                  scope="partial metadata only; existing guard with empty keeper endpoint list; no model content or fresh GPU health")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
