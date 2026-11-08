"""Verify a bounded recovery delta without overwriting failed startup facts."""

import argparse
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    previous, current = [json.loads(p.read_text()) for p in (args.previous, args.current)]
    raw = Path(current["owned_run"]) / "summary.json"
    checks = {"same_owned_run": previous["owned_run"] == current["owned_run"],
              "original_raw_unchanged": current["original_summary_unchanged"] and sha(raw) == previous["source_summary_sha"] == current["source_summary_sha"],
              "raw_still_failed": json.loads(raw.read_text())["status"] == "failed",
              "same_pending_exact_targets": current["pending_exact_targets"] == previous["pending_exact_targets"],
              "cleanup_remains_unconfirmed": current["exact_remote_cleanup_passed"] is False,
              "source_snapshot_sha": sha(args.current.parent / "source.py") == current["script_sha256"],
              "zero_new_workloads": current["gpu_workloads"] == 0,
              "both_ssh_control_routes_failed": len(current["ssh_attempts"]) == 2 and all(r["returncode"] == 255 for r in current["ssh_attempts"]),
              "both_keeper_http_routes_failed": len(current["http"]) == 2 and all(r["passed"] is False for r in current["http"]),
              "icmp_only_remains_up": len(current["icmp"]) == 2 and all(r["returncode"] == 0 for r in current["icmp"])}
    for rank in (2, 3):
        spec = Path(current["owned_run"]) / f"rank_{rank}.process.json"
        expected = current["exact_targets"]["specs"][str(rank)]
        checks[f"rank{rank}/spec_state_frozen"] = sha(spec) == expected["spec_sha"] and sha(spec.with_suffix(".pid.json")) == expected["state_sha"]
    result = {"status": "passed" if all(checks.values()) else "failed", "checks": checks,
              "failed": [k for k, v in checks.items() if not v], "own_remote_cleanup_confirmed": False,
              "runtime_accepted": False, "pending_exact_targets": current["pending_exact_targets"],
              "previous_ref": {"path": str(args.previous), "sha256": sha(args.previous)},
              "current_ref": {"path": str(args.current), "sha256": sha(args.current)}, "gpu_operations": 0}
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "passed": sum(checks.values()), "total": len(checks), "failed": result["failed"]}))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
