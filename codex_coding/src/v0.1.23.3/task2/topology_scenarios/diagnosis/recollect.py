"""Recover post-exit NCCL evidence without changing the original GPU run."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import time

import run_diagnosis as driver
from fluidckpt.benchmarks.topology_calibration import shared_gate, validate_process_exit


def remote_log(path, pid):
    raw = path.read_bytes()
    stat = path.stat()
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
        "stat": {"device": stat.st_dev, "inode": stat.st_ino, "mtime_ns": stat.st_mtime_ns},
        "clock_domain": driver.inventory.clock_domain(), "collected_monotonic_ns": time.monotonic_ns(),
        "process": driver.inventory.proc_state(pid), "content_base64": base64.b64encode(raw).decode()}


def recollect(source, out):
    source, out = source.resolve(), out.resolve()
    if driver.inventory.RESULT_ROOT not in source.parents or not source.name.startswith("diagnosis_probe_"):
        raise ValueError("source must be an original isolated diagnosis run")
    if driver.inventory.RESULT_ROOT not in out.parents or not out.name.startswith("diagnosis_recollect_"):
        raise ValueError("use a new topology_scenarios/diagnosis_recollect_* directory")
    out.mkdir(parents=True, exist_ok=False)
    source_summary = source / "summary.json"
    original_hash = driver.inventory.digest(source_summary)
    original = json.loads(source_summary.read_text())
    if not original["cleanup"]["passed"] or not original["before_after_preservation"]["passed"]:
        raise ValueError("original cleanup/preservation did not pass")
    if not original["code_preservation"]["passed"] or not original["input_preservation"]["passed"]:
        raise ValueError("original code/input preservation did not pass")
    frozen_code_checks = {path: driver.inventory.digest(driver.inventory.ROOT / path) == sha
                          for path, sha in original["code_sha256"].items()}
    frozen_input_checks = {path: driver.inventory.digest(path) == sha
                           for path, sha in original["input_sha256"].items()}
    if not all(frozen_code_checks.values()) or not all(frozen_input_checks.values()):
        raise ValueError("a named frozen source code or input file changed")
    state = driver.inventory.load_session(original["session"])
    logs, bindings = [], []
    for entry in sorted(original["processes"], key=lambda e: e["rank"]):
        rank, node = entry["rank"], entry["node"]
        expected = entry["start_reply"]
        spec_path = Path(entry["spec"])
        spec = json.loads(spec_path.read_text())
        registered = json.loads(spec_path.with_suffix(".pid.json").read_text())
        log_path = Path(spec["env"]["NCCL_DEBUG_FILE"].replace("%p", str(expected["pid"])))
        if "%" in str(log_path) or log_path.parent != source:
            raise ValueError("unresolved or non-owned log path")
        fetched = json.loads(driver.inventory.remote(state, node, ["python3", str(Path(__file__).resolve()),
            "--remote-log", str(log_path), "--pid", str(expected["pid"])]))
        validate_process_exit(expected, registered, fetched["process"])
        raw = base64.b64decode(fetched.pop("content_base64"), validate=True)
        if hashlib.sha256(raw).hexdigest() != fetched["sha256"]:
            raise ValueError("remote content hash mismatch")
        local_hash = driver.inventory.digest(log_path) if log_path.exists() else None
        if local_hash != fetched["sha256"]:
            raise ValueError("post-exit local and remote log content differ")
        target = out / log_path.name
        target.write_bytes(raw)
        if not any(f":{expected['pid']}:" in line and "NET/IB : Using" in line
                   for line in raw.decode(errors="replace").splitlines()):
            raise ValueError("log lacks the recorded PID's actual HCA initialization")
        logs.append(dict(fetched, rank=rank, node=node, local_sha256=local_hash,
                         saved_log=str(target), saved_sha256=driver.inventory.digest(target)))
        bindings.append(driver.binding(out, rank, spec["env"]["NCCL_IB_HCA"], spec["env"]["NCCL_SOCKET_IFNAME"]))
    prereg = json.loads((source / "protocol.json").read_text())
    observations, _, ranks = driver.BASE_COLLECT(source, prereg, "shca_0", "ib0")
    for observation in observations:
        observation["actual_nic_payload_evidence"] = driver.per_port_payload(observation["flows"], bindings)
    def without_payload(rows):
        return [{k: v for k, v in row.items() if k != "actual_nic_payload_evidence"} for row in rows]
    unchanged = without_payload(observations) == without_payload(original["observations"])
    b_loc = original.get("diagnosis_control", {}).get("flow_b_gdr_level_override") == "LOC"
    intervention = (not b_loc or (
        all(b["gdr_disabled_observed"] and not b["gdr_enabled_observed"] and not b["gdr_channel_lines"]
            for b in bindings if b["rank"] in (1, 3)) and
        all(b["gdr_enabled_observed"] and b["gdr_channel_lines"]
            for b in bindings if b["rank"] in (0, 2))))
    checks = {"all_rank_bindings_passed": all(b["passed"] for b in bindings),
        "requested_b_loc_and_unchanged_a_gdr_observed": bool(intervention),
        "all_actual_port_payloads_passed": all(o["actual_nic_payload_evidence"]["passed"] for o in observations),
        "all_background_estimates_below_5pct": all(o["actual_nic_payload_evidence"]["background_fraction_estimate"] <= .05
                                                    for o in observations),
        "timing_and_content_observations_unchanged": unchanged,
        "original_summary_unchanged": driver.inventory.digest(source_summary) == original_hash,
        "named_frozen_code_unchanged": all(frozen_code_checks.values()),
        "named_frozen_inputs_unchanged": all(frozen_input_checks.values()),
        "original_cleanup_and_protection_passed": True}
    result = dict(original)
    result.pop("error", None)
    result.update(status="passed" if all(checks.values()) else "failed",
        out=str(out), observations=observations, binding=bindings,
        source_driver_status=original["status"], source_driver_error=original.get("error"),
        source_run=str(source), source_summary_sha256=original_hash,
        recollection={"read_only": True, "new_gpu_observations": 0, "checks": checks,
            "logs": logs, "limitation": "complete after process exit; original missing logs do not distinguish logger buffering from filesystem visibility"},
        shared_resource=shared_gate(observations, all(b["passed"] for b in bindings),
            original["before_after_preservation"]["passed"], original["cleanup"]["passed"]))
    driver.inventory.write(out / "summary.json", result)
    sources = [*source.glob("*.json"), *[Path(item["path"]) for item in logs]]
    manifest = {"schema_version": "contention_post_exit_recollection.v1", "read_only": True,
        "command": sys.argv, "script_sha256": driver.inventory.digest(__file__),
        "source_run": str(source), "source_summary_sha256": original_hash,
        "new_gpu_observations": 0, "source_samples_per_configuration": 1,
        "source_files_sha256": {str(p): driver.inventory.digest(p) for p in sorted(sources)},
        "derived_files_sha256": {str(p): driver.inventory.digest(p) for p in sorted(out.glob("*"))},
        "frozen_code_checks": frozen_code_checks, "frozen_input_checks": frozen_input_checks,
        "checks": checks}
    driver.inventory.write(out / "manifest.json", manifest)
    return result


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--remote-log", type=Path)
    parser.add_argument("--pid", type=int)
    args = parser.parse_args()
    if args.remote_log:
        if args.pid is None:
            parser.error("--pid is required for read-only remote log evidence")
        print(json.dumps(remote_log(args.remote_log, args.pid)))
        return
    if args.source is None or args.out is None:
        parser.error("--source and --out are required for recollection")
    result = recollect(args.source, args.out)
    print(json.dumps({"status": result["status"], "out": result["out"],
                      "checks": result["recollection"]["checks"]}))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
