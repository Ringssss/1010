"""Freeze parent-executed SYS_NICE setup specs from the existing isolated wrapper."""
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
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run", default="minimal_measure_r01")
    args = parser.parse_args()
    import re
    if not re.fullmatch(r"[a-z0-9_]+", args.run):
        raise ValueError("safe run name required")
    args.out.mkdir(parents=True, exist_ok=False)
    old = (ROOT / "codex_coding/src/v0.1.23.3/task2/three_node_recovery/inventory/fresh_host.py").read_text()
    new = old.replace('"--cap-add","IPC_LOCK","--ulimit"', '"--cap-add","IPC_LOCK","--cap-add","SYS_NICE","--ulimit"')
    new = new.replace('not str(rel).startswith("codex_coding/")', 'not str(rel).startswith(("codex_coding/", "main_lib/fluidckpt/"))')
    if new == old or new.count('"--cap-add","SYS_NICE"') != 1:
        raise ValueError("frozen wrapper shape differs")
    wrapper = new.encode()
    (args.out / "measurement_host.source.py").write_bytes(wrapper)
    (args.out / "wrapper.diff").write_text("".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile="frozen_fresh_host.py", tofile="measurement_host.source.py")))
    assets = []
    def asset(rel, raw=None):
        data = (ROOT / rel).read_bytes() if raw is None else raw
        assets.append({"relative_path": str(rel), "sha256": sha(data), "base64": base64.b64encode(data).decode()})
    for rel in sorted((ROOT / "main_lib/fluidckpt").rglob("*.py")):
        asset(rel.relative_to(ROOT))
    asset(Path("codex_coding/src/v0.1.23.3/task2/three_node_recovery/admission28/runtime_gate_r03.py"))
    asset(Path("codex_coding/src/v0.1.23.3/task2/minimal_calibration/measure/measurement_driver.py"))
    for node, gpu, bdf in (("node28", 0, "0000:09:00.0"), ("node30", 4, "0000:85:00.0")):
        source = ROOT / "codex_coding/results/v0.1.23.3/task2/three_node_recovery" / ("admission28_guard_after_nccl_r03/effective.spec.json" if node == "node28" else "diagnosis30_guard_before_control_r01/effective.spec.json")
        spec = json.loads(source.read_text())
        spec.pop("expected_container_id", None)
        spec.pop("expected_container_started", None)
        xmlrel = Path("codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_xml_2830_gdr0_r01") / (node + ".physical.gdr0.xml")
        manifestrel = xmlrel.with_name(node + ".manifest.json")
        manifest = json.loads((ROOT / manifestrel).read_text())
        manifest["input_file"] = str(ROOT / xmlrel)
        node_assets = list(assets)
        data = (ROOT / xmlrel).read_bytes()
        node_assets.append({"relative_path": str(xmlrel), "sha256": sha(data), "base64": base64.b64encode(data).decode()})
        data = (json.dumps(manifest, indent=2) + "\n").encode()
        node_assets.append({"relative_path": str(manifestrel), "sha256": sha(data), "base64": base64.b64encode(data).decode()})
        spec.update(container_name=f"zjn-fluid-three-node-{args.run}-n{node[-2:]}",
                    stage=f"/tmp/fluidckpt_three_node/{args.run}_n{node[-2:]}",
                    gpu=gpu, bdf=bdf, host_wrapper_sha256=sha(wrapper), assets=node_assets,
                    setup_mode="parent explicitly executes frozen SYS_NICE wrapper; no GPU startup",
                    cap_add_expected=["IPC_LOCK", "SYS_NICE"], ipc_mode_expected="private",
                    authorization="parent selected candidate GPU0→GPU4; fresh qualification required before calibration",
                    fresh_admission={"path": str(ROOT / f"codex_coding/results/v0.1.23.3/task2/minimal_calibration/measure_refresh_r01/{node}/stdout.json"),
                                     "sha256": sha((ROOT / f"codex_coding/results/v0.1.23.3/task2/minimal_calibration/measure_refresh_r01/{node}/stdout.json").read_bytes())},
                    experiment_increment_budget={"HBM_bytes_per_node": 2 << 30, "DRAM_bytes_per_node": 2 << 30, "container_bytes": 8 << 30},
                    performance_ready=False, original_and_old_stage_files_modified=False)
        (args.out / (node + ".spec.json")).write_text(json.dumps(spec, indent=2) + "\n")
    (args.out / "manifest.json").write_text(json.dumps({"parent_execution_only": True, "gpu_calls": 0,
        "wrapper_sha256": sha(wrapper), "base_wrapper_sha256": sha(old.encode()), "source_asset_count": len(assets),
        "configuration": "private IPC, SYS_NICE, 8GiB container, 1GiB shm for code/logs only; no payload files",
        "runtime_qualification_output_rule": "frozen r03 writes fresh three_node_recovery/probe_*; manifest links into minimal_calibration",
        "microbench_profile_eligible": False}, indent=2) + "\n")
    print(json.dumps({"out": str(args.out), "source_asset_count": len(assets), "gpu_calls": 0}))


if __name__ == "__main__":
    main()
