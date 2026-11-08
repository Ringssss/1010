"""Compare frozen host guards and keeper catalogs without contacting any node."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / "codex_coding/results/v0.1.23.3/task2/three_node_recovery"
GUARD = 2 << 30
FREE_TOLERANCE = 128 << 20


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def keeper_signature(body):
    keys = ("worker", "node", "gpu", "numa", "epoch", "session", "pid", "budget_bytes", "used_bytes", "replicas", "transactions", "transaction_phases", "staging_bytes", "hbm_allocated_bytes", "hbm_reserved_bytes")
    return hashlib.sha256(json.dumps({k: body.get(k) for k in keys}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def memory_headroom(data):
    mem = {line.split()[0].rstrip(":"): int(line.split()[1]) * 1024 for line in data["host_memory"].splitlines() if len(line.split()) > 1 and line.split()[1].isdigit()}
    amount = mem["MemAvailable"]
    cg = data["container_cgroup"]["files"]
    if cg["memory.max"] != "max":
        amount = min(amount, int(cg["memory.max"]) - int(cg["memory.current"]))
    return amount


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out = args.out.resolve()
    if args.out.parent != RESULTS or not args.out.name.startswith("audit_"):
        parser.error("require a new local versioned audit directory")
    spec, before, after = [json.loads(p.read_text()) for p in (args.spec, args.before, args.after)]
    checks = {}
    for stage, data in (("before", before), ("after", after)):
        checks[f"{stage}/host_identity"] = data["hostname"] == spec["hostname"] and data["boot_id"] == spec["boot_id"]
        ci = data["container"]
        checks[f"{stage}/container_running"] = ci["State"]["Running"] is True and ci["image_id"] == spec["image_id"]
        checks[f"{stage}/container_guards"] = data.get("container_identity_checks") and all(data["container_identity_checks"].values())
        checks[f"{stage}/assets"] = all(data["source_assets"][a["relative_path"]]["sha256"] == a["sha256"] for a in spec["assets"])
        checks[f"{stage}/owner_visibility"] = not data["visibility_errors"]
        checks[f"{stage}/host_cgroup_headroom"] = memory_headroom(data) >= GUARD + (512 << 20)
        owners = {r["pid"]: r for r in data["owners"]}
        for expected in spec.get("known_live_workers", []):
            row = owners.get(expected["pid"], {})
            checks[f"{stage}/known_worker/{expected['pid']}"] = row.get("state") not in (None, "Z") and row.get("start_time_ticks") == expected["start_time_ticks"] and row.get("cgroup") == expected["cgroup"]
        keepers = {r["endpoint"]: r for r in data["keepers"]}
        checks[f"{stage}/keeper_scope"] = set(keepers) == set(spec.get("keeper_endpoints", []))
        for endpoint, row in keepers.items():
            body = row.get("body", {})
            checks[f"{stage}/{endpoint}/identity_idle"] = row.get("passed") is True and body.get("node") == spec["node"] and body.get("gpu") == int(endpoint.rsplit(":", 1)[1]) % 100 and not body.get("transactions")
            checks[f"{stage}/{endpoint}/catalog_digest"] = row.get("catalog_sha256") == keeper_signature(body)
            checks[f"{stage}/{endpoint}/lease_zero"] = all(r.get("leases") == 0 for r in body.get("replicas", []))
    for key in ("id", "image_id"):
        checks[f"container_same/{key}"] = before["container"][key] == after["container"][key]
    for key in ("StartedAt", "Pid"):
        checks[f"container_same/{key}"] = before["container"]["State"][key] == after["container"]["State"][key]
    old_protected = {r["name"]: r for r in before["protected_containers"]}
    new_protected = {r["name"]: r for r in after["protected_containers"]}
    checks["protected_container_set"] = set(old_protected) == set(new_protected)
    for name, old in old_protected.items():
        new = new_protected.get(name, {})
        checks[f"protected_container/{name}"] = old.get("id") == new.get("id") and old.get("image_id") == new.get("image_id") and all(old.get("State", {}).get(k) == new.get("State", {}).get(k) for k in ("StartedAt", "Pid", "Running"))
    old_keepers = {r["endpoint"]: r for r in before["keepers"]}
    new_keepers = {r["endpoint"]: r for r in after["keepers"]}
    free = []
    for endpoint, row in old_keepers.items():
        new = new_keepers.get(endpoint, {})
        checks[f"catalog_preserved/{endpoint}"] = row.get("catalog_sha256") == new.get("catalog_sha256")
        if row.get("body", {}).get("gpu") == spec["gpu"]:
            old_free = row["body"]["headroom_bytes"]["hbm"]
            new_free = new["body"]["headroom_bytes"]["hbm"]
            checks[f"selected_keeper_hip_free/{endpoint}"] = old_free > GUARD and max(0, old_free - new_free) <= FREE_TOLERANCE
            free.append({"endpoint": endpoint, "before_hip_free_bytes": old_free, "after_hip_free_bytes": new_free, "delta_bytes": new_free - old_free, "source": "existing initialized keeper physical free; not sysfs arithmetic"})
    owner_ids = lambda data: {(r["pid"], r["start_time_ticks"], r["cgroup"]) for r in data["owners"]}
    old_owners, new_owners = owner_ids(before), owner_ids(after)
    result = {"passed": all(bool(v) for v in checks.values()), "checks": checks, "failed": [k for k, v in checks.items() if not v], "selected_keeper_hip_free": free, "selected_gpu_sysfs_observations": {"before": before["selected_gpu_sysfs"], "after": after["selected_gpu_sysfs"]}, "visible_kfd_owner_delta": {"removed_identities": sorted(old_owners-new_owners), "added_identities": sorted(new_owners-old_owners), "stable_identity_count": len(old_owners & new_owners), "scope": "visible KFD PID/start/cgroup facts; scheduler R/S changes are not ownership changes"}, "owner_inventory_complete": False, "exclusive": False, "scope": "this node's exact known keeper catalog/container/PID guards; no weight reread, no full-cluster protection or performance claim", "gpu_operations": 0, "performance_ready": False}
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "source.py").write_bytes(Path(__file__).read_bytes())
    result["input_refs"] = []
    for label, path in (("spec", args.spec), ("before", args.before), ("after", args.after)):
        target = args.out / f"{label}.json"
        target.write_bytes(path.read_bytes())
        result["input_refs"].append({"path": str(path.resolve()), "saved": str(target), "sha256": sha(target)})
    (args.out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": result["passed"], "failed": result["failed"], "selected_keeper_hip_free": free}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
