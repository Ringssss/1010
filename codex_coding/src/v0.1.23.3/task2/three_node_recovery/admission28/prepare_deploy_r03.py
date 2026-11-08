"""Prepare one new runtime file per node and exact IB compatibility commands."""
import base64
import hashlib
import json
from pathlib import Path
import shlex

ROOT = Path(__file__).resolve().parents[6]
REL_RESULT = Path("codex_coding/results/v0.1.23.3/task2/three_node_recovery")
REL_GATE = Path("codex_coding/src/v0.1.23.3/task2/three_node_recovery/admission28/runtime_gate_r03.py")
GATE_SHA = "d1ae5e31c3bf801d36011cbb9a6e911b400fd4e64c6689c8c742d4aa78d4f058"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    result = ROOT / REL_RESULT
    previous = result / "admission28_nccl_deploy_r02"
    out = result / "admission28_nccl_deploy_r03"
    out.mkdir(exist_ok=True)
    assert not (out / "launches.json").exists()
    raw = (ROOT / REL_GATE).read_bytes()
    assert sha(raw) == GATE_SHA
    rows = []
    for original in json.loads((previous / "launches.json").read_text())["ranks"]:
        node, root = original["node"], Path(original["root"])
        old_bundle = json.loads((previous / (node + ".bundle.json")).read_text())
        bundle = {key: value for key, value in old_bundle.items() if key != "assets"}
        bundle["assets"] = [{"relative_path": str(REL_GATE), "sha256": GATE_SHA,
                             "base64": base64.b64encode(raw).decode(), "bytes": len(raw)}]
        bundle["reused_xml_manifest"] = old_bundle["reused_xml_manifest"]
        bundle["stage_scope"] = "one new runtime_gate_r03.py only; preserve existing 97a source/XML/manifest"
        bundle_path = out / (node + ".bundle.json")
        bundle_path.write_text(json.dumps(bundle, indent=2) + "\n")
        remote = list(original["remote_argv"])
        assert "NCCL_IB_HCA=shca_0" in remote and "NCCL_NET=IBext_v8" in remote
        remote[remote.index("python3")+1] = str(root / REL_GATE)
        run_name = "probe_nccl_2830_" + node + "_r03"
        remote[remote.index("--out")+1] = str(root / REL_RESULT / run_name)
        outer = list(original["outer_argv"])
        outer[-1] = shlex.join(remote)
        command = "#!/bin/bash\nset -u\nulimit -c 0\n" + shlex.join(outer) + "\n"
        (out / (node + ".command.sh")).write_text(command)
        rows.append({**original, "bundle": str(bundle_path), "bundle_sha256": sha(bundle_path.read_bytes()),
                     "command_sha256": sha(command.encode()), "outer_argv": outer, "remote_argv": remote,
                     "run_name": run_name, "source_sha256": GATE_SHA, "executed": False})
    (out / "runtime_gate_r02.source.py").write_bytes(raw)
    (out / "launches.json").write_text(json.dumps({"ranks": rows, "runtime_source_sha256": GATE_SHA,
       "configuration": "historically compatible IB group; not a single-variable causal comparison",
       "NCCL_IB_HCA": "shca_0", "required_actual_HCA_port": "shca_0:1", "NCCL_SOCKET_IFNAME": "ib0",
       "NCCL_NET_PLUGIN": "librccl-net-shca.so", "HSA_FORCE_FINE_GRAIN_PCIE": "1", "NCCL_NET": "IBext_v8",
       "init_copy_seconds": 60, "nccl_init_seconds": 60, "nccl_total_seconds": 90,
       "outer_seconds": 100, "communicator_seconds": 20, "gpu_operations": 0,
       "cpu_evidence": str(result / "admission28_validation_r03_cpu_r02/summary.json")}, indent=2) + "\n")
    print(json.dumps({"out": str(out), "runtime_source_sha256": GATE_SHA, "gpu_operations": 0}))


if __name__ == "__main__":
    main()
