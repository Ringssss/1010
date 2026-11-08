"""Audit existing isolated setup and one preflight from frozen local JSON."""
import argparse
import base64
import hashlib
import json
from pathlib import Path

from review_outer_guard import RESULTS, GUARD, memory_headroom, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", type=Path, required=True)
    parser.add_argument("--guard", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out = args.out.resolve()
    if args.out.parent != RESULTS or not args.out.name.startswith("audit_"):
        parser.error("require fresh local versioned audit output")
    spec = json.loads((args.guard / "effective.spec.json").read_text())
    guard = json.loads((args.guard / "stdout.json").read_text())
    setup = json.loads((args.setup / "stdout.json").read_text())
    setup_result = json.loads((args.setup / "result.json").read_text())
    guard_result = json.loads((args.guard / "result.json").read_text())
    ci = guard["container"]
    hc = ci["HostConfig"]
    checks = {
        "setup_transport_and_docker_success": setup_result["returncode"] == 0 and setup["run"]["returncode"] == 0,
        "guard_transport_success": guard_result["returncode"] == 0 and guard_result["identity_checks_passed"] is True,
        "boot_hostname": guard["boot_id"] == spec["boot_id"] and guard["hostname"] == spec["hostname"],
        "container_id_started": ci["id"] == spec["expected_container_id"] == setup["container"]["id"] and ci["State"]["StartedAt"] == spec["expected_container_started"] == setup["container"]["State"]["StartedAt"],
        "container_running_image": ci["State"]["Running"] is True and ci["image_id"] == spec["image_id"],
        "guard_source_frozen": sha(args.guard / "fresh_host.source.py") == spec["host_wrapper_sha256"],
        "isolated_cgroup_private_ipc": hc["Memory"] == 8 << 30 and hc["IpcMode"] == "private" and hc["ShmSize"] == 1 << 30 and hc["Privileged"] is False and hc["PidsLimit"] == 512,
        "core_zero": any(r["Name"] == "core" and r["Soft"] == r["Hard"] == 0 for r in hc["Ulimits"]),
        "hyhal_readonly": any(r["Destination"] == "/opt/hyhal" and r["Source"] == "/opt/hyhal" and r["RW"] is False for r in ci["mounts"]),
        "private_stage_mount": any(r["Source"] == spec["stage"] + "/project" and r["Destination"] == spec["project_container_path"] for r in ci["mounts"]),
        "host_cgroup_guard": memory_headroom(guard) >= GUARD + (512 << 20),
        "gpu_api_zero": guard["gpu_api_calls"] == 0,
        "visibility_errors_absent": not guard["visibility_errors"],
        "identity_checks": bool(guard.get("container_identity_checks")) and all(guard["container_identity_checks"].values()),
    }
    for asset in spec["assets"]:
        name = asset["relative_path"]
        checks[f"asset/{name}"] = hashlib.sha256(base64.b64decode(asset["base64"], validate=True)).hexdigest() == asset["sha256"] == guard["source_assets"][name]["sha256"]
    owners = {r["pid"]: r for r in guard["owners"]}
    for expected in spec.get("known_live_workers", []):
        row = owners.get(expected["pid"], {})
        checks[f"retained_worker/{expected['pid']}"] = row.get("state") not in (None, "Z") and row.get("start_time_ticks") == expected["start_time_ticks"] and row.get("cgroup") == expected["cgroup"]
    checks["keeper_endpoint_scope"] = {r["endpoint"] for r in guard["keepers"]} == set(spec["keeper_endpoints"])
    checks["keeper_read_success"] = all(r["passed"] is True for r in guard["keepers"])
    result = {"metadata_preflight_passed": all(checks.values()), "checks": checks, "failed": [k for k, v in checks.items() if not v], "node": spec["node"], "selected_bdf": spec["bdf"], "selected_gpu_sysfs": guard["selected_gpu_sysfs"], "host_cgroup_headroom_bytes": memory_headroom(guard), "visible_kfd_owner_count": len(guard["owners"]), "owner_inventory_complete": False, "exclusive": False, "hip_free_bytes": None, "payload_admission": "requires fresh child actual HIP BDF and HIP free guard", "postflight_verified": False, "runtime_ready": False, "performance_ready": False, "gpu_operations": 0}
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "source.py").write_bytes(Path(__file__).read_bytes())
    (args.out / "helper.source.py").write_bytes(Path(__file__).with_name("review_outer_guard.py").read_bytes())
    result["input_refs"] = []
    for label, root in (("setup", args.setup), ("guard", args.guard)):
        for name in ("effective.spec.json", "stdout.json", "result.json", "fresh_host.source.py"):
            source = root / name
            destination = args.out / (label + "." + name)
            destination.write_bytes(source.read_bytes())
            result["input_refs"].append({"path": str(source.resolve()), "saved": str(destination), "sha256": sha(destination)})
    (args.out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in ("checks", "input_refs")}, ensure_ascii=False))
    if not result["metadata_preflight_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
