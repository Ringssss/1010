"""Freeze one node28 r02 spec without remote changes or GPU operations."""
import ast
import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
RESULT = ROOT / "codex_coding/results/v0.1.23.3/task2/three_node_recovery"
SOURCE = ROOT / "codex_coding/src/v0.1.23.3/task2/three_node_recovery"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    prior = RESULT / "inventory_setup_plan_r06/node28.spec.json"
    refresh = RESULT / "admission28_refresh_r01/stdout.json"
    spec = json.loads(prior.read_text())
    live = json.loads(refresh.read_text())
    assert live["hostname"] == spec["hostname"] and live["boot_id"] == spec["boot_id"]
    assert all(all(row["checks"].values()) for row in live["protected_containers"])
    assert all(row["owner_matches"] for row in live["protected_processes"])
    assert all(row["passed"] for row in live["keepers"])
    out = RESULT / "admission28_plan_r01"
    out.mkdir(exist_ok=False)
    spec.update(container_name="zjn-fluid-three-node-isolated_r02-n28",
                stage="/tmp/fluidckpt_three_node/isolated_r02_n28",
                fresh_admission={"path": str(refresh.relative_to(ROOT)), "sha256": sha(refresh.read_bytes()),
                                 "sampled_utc": live["ended_utc"], "hip_free_bytes": None},
                protected_container_expectations=[{"id": row["expected_id"], "started": row["expected_started"]} for row in live["protected_containers"]],
                expected_keeper_catalogs={row["endpoint"]: row["catalog_sha256"] for row in live["keepers"]},
                baseline_configuration="private IPC; same setup flags as node30 r01; cross-node descriptive comparison")
    owner = live["protected_processes"][0]
    spec["known_live_workers"].insert(0, {"pid": owner["pid"], "start_time_ticks": owner["start_time_ticks"], "cgroup": owner["cgroup"], "role": "memory_owner"})
    for asset in spec["assets"]:
        raw = base64.b64decode(asset["base64"], validate=True)
        assert sha(raw) == asset["sha256"]
        if asset["relative_path"].endswith("init_gate.py") or asset["relative_path"].endswith("container_namespace.py"):
            assert sha((ROOT / asset["relative_path"]).read_bytes()) == asset["sha256"]
    assert sha((SOURCE / "inventory/fresh_host.py").read_bytes()) == spec["host_wrapper_sha256"]
    (out / "node28.spec.json").write_text(json.dumps(spec, indent=2) + "\n")
    frozen = {}
    for relative in ("inventory/fresh_host.py", "inventory/fresh_container.py", "probe/init_gate.py", "probe/run_gate.py", "probe/host_gate.py"):
        raw = (SOURCE / relative).read_bytes()
        ast.parse(raw)
        (out / relative.replace("/", ".")).write_bytes(raw)
        frozen[relative] = sha(raw)
    (out / "source_manifest.json").write_text(json.dumps({"source_sha256": frozen, "prior_spec_sha256": sha(prior.read_bytes()), "new_spec_sha256": sha((out / "node28.spec.json").read_bytes()), "cpu_ast_passed": True, "remote_operations": 0, "gpu_operations": 0}, indent=2) + "\n")
    print(json.dumps({"spec": str(out / "node28.spec.json"), "ready_for_parent_setup_review": True}))


if __name__ == "__main__":
    main()
