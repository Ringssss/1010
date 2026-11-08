"""Freeze delivery assets and launch JSON against the actually deployed r01 guard."""
import argparse
import base64
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--delivery", type=Path, required=True)
    parser.add_argument("--launch-plan", type=Path, required=True)
    parser.add_argument("--guard-base", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    delivery_raw, launch_raw = args.delivery.read_bytes(), args.launch_plan.read_bytes()
    delivery = json.loads(delivery_raw)
    launch = json.loads(launch_raw)
    expected_launch = delivery["launch_plan_ref"]
    if sha(launch_raw) != expected_launch["sha256"] or args.launch_plan.resolve() != Path(expected_launch["path"]).resolve():
        raise ValueError("launch plan differs from frozen delivery")
    if len(delivery["assets"]) != 9:
        raise ValueError("require the final nine delivery assets")
    args.out.mkdir(parents=True, exist_ok=False)
    prior_helper = Path(__file__).with_name("stage_measurement_host.py").read_text()
    helper = prior_helper.replace('not str(rel).startswith("codex_coding/")',
                                  'not str(rel).startswith(("codex_coding/", "main_lib/fluidckpt/"))')
    if helper == prior_helper:
        raise ValueError("atomic helper whitelist shape differs")
    helper_raw = helper.encode()
    (args.out / "stage_integration_host.source.py").write_bytes(helper_raw)
    (args.out / "helper.diff").write_text("".join(difflib.unified_diff(prior_helper.splitlines(True), helper.splitlines(True),
        fromfile="stage_measurement_host.py", tofile="stage_integration_host.source.py")))
    frozen = []
    for item in delivery["assets"]:
        rel = Path(item["path"])
        if rel.is_absolute() or ".." in rel.parts or not str(rel).startswith(("main_lib/fluidckpt/", "codex_coding/")):
            raise ValueError("asset outside authorized project roots")
        raw = (ROOT / rel).read_bytes()
        if sha(raw) != item["sha256"] or len(raw) != item["bytes"]:
            raise ValueError("delivery source changed: " + str(rel))
        if rel.suffix == ".py":
            compile(raw, str(rel), "exec")
        frozen.append({"relative_path": str(rel), "sha256": sha(raw), "base64": base64.b64encode(raw).decode()})
    launch_rel = str(args.launch_plan.resolve().relative_to(ROOT))
    frozen.append({"relative_path": launch_rel, "sha256": sha(launch_raw), "base64": base64.b64encode(launch_raw).decode()})
    worker_by_node = {row["node"]: row for row in launch["workers"]}
    reports = []
    for node in ("node28", "node30"):
        base_path = args.guard_base / (node + ".latest.guard.spec.json")
        guard = json.loads(base_path.read_text())
        worker = worker_by_node[node]
        if (worker["container_id"], worker["container_started_at"], worker["boot_id"]) != (
                guard["expected_container_id"], guard["expected_container_started"], guard["boot_id"]):
            raise ValueError("launch plan and deployed guard identities differ")
        prior = {row["relative_path"]: row for row in guard["assets"]}
        for row in prior.values():
            if sha(base64.b64decode(row["base64"], validate=True)) != row["sha256"]:
                raise ValueError("deployed guard asset bytes differ from its own SHA")
        untouched_driver = "codex_coding/src/v0.1.23.3/task2/minimal_calibration/measure/measurement_driver.py"
        if prior[untouched_driver]["sha256"] != "feda34abf25949cf2756513d12ebde20cb4d188bafe54dc3afee27cae86490e9":
            raise ValueError("deployed measurement source is not original feda r01")
        delta = []
        audit = []
        for item in frozen:
            old = prior.get(item["relative_path"])
            row = dict(item, expected_before_sha256=old["sha256"] if old else None)
            delta.append(row)
            audit.append({"relative_path": row["relative_path"], "expected_before_sha256": row["expected_before_sha256"],
                          "after_sha256": row["sha256"], "bytes": len(base64.b64decode(row["base64"]))})
            if old is None:
                guard["assets"].append(dict(item))
            else:
                old.update(item)
        bundle = {"node": node, "hostname": guard["hostname"], "boot_id": guard["boot_id"],
            "container_id": guard["expected_container_id"], "container_started_at": guard["expected_container_started"],
            "image_id": guard["image_id"], "stage": guard["stage"], "assets": delta}
        bundle_path = args.out / (node + ".bundle.json")
        bundle_path.write_text(json.dumps(bundle, indent=2) + "\n")
        guard_path = args.out / (node + ".latest.guard.spec.json")
        guard_path.write_text(json.dumps(guard, indent=2) + "\n")
        remote_helper = "/tmp/fluidckpt_minimal_calibration_r01_bootstrap/stage_integration_host.source.py"
        remote_bundle = "/tmp/fluidckpt_minimal_calibration_r01_bootstrap/" + node + ".integration.bundle.json"
        command = {"upload_helper": str((args.out / "stage_integration_host.source.py").resolve()),
            "upload_bundle": str(bundle_path.resolve()), "remote_helper": remote_helper, "remote_bundle": remote_bundle,
            "argv": ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ConnectionAttempts=1",
                     "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no", "-o", "ServerAliveInterval=3",
                     "-o", "ServerAliveCountMax=1", "-o", "Hostname=12.11.3." + node[-2:], node,
                     "python3 " + remote_helper + " " + remote_bundle]}
        (args.out / (node + ".deploy.command.json")).write_text(json.dumps(command, indent=2) + "\n")
        reports.append({"node": node, "delta_asset_count": len(delta), "replaced_assets": sum(r["expected_before_sha256"] is not None for r in delta),
            "new_assets": sum(r["expected_before_sha256"] is None for r in delta), "asset_audit": audit,
            "bundle_sha256": sha(bundle_path.read_bytes()), "latest_guard_sha256": sha(guard_path.read_bytes()),
            "base_guard_ref": {"path": str(base_path.resolve()), "sha256": sha(base_path.read_bytes())}})
    report = {"schema_version": "fluidckpt.minimal_calibration.integration_deploy_plan.v1", "nodes": reports,
        "delivery_ref": {"path": str(args.delivery.resolve()), "sha256": sha(delivery_raw)},
        "launch_plan_ref": {"path": str(args.launch_plan.resolve()), "sha256": sha(launch_raw)},
        "helper_sha256": sha(helper_raw), "source_assets_verified_and_compile_passed": True,
        "delivery_asset_count": 9, "launch_JSON_asset_count": 1, "GPU_calls": 0, "remote_operations": 0,
        "deployment_requires_parent_execution": True, "feda_measurement_driver_unchanged": True, "256_driver_deployed": False}
    (args.out / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"out": str(args.out), "delta_assets_per_node": 10, "source_SHA_compile_passed": True,
                      "GPU_calls": 0, "remote_operations": 0}))


if __name__ == "__main__":
    main()
