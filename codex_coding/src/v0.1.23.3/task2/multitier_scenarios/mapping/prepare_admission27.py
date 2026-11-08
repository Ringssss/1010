"""Freeze a thin node27 setup adaptation and unchanged successful 60s copy gate."""
import base64
import difflib
import hashlib
import json
from pathlib import Path
import shlex

ROOT = Path(__file__).resolve().parents[6]
BASE = ROOT / "codex_coding/results/v0.1.23.3/task2/multitier_scenarios"

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def main():
    out = BASE / "mapping_admission27_plan_r02"
    out.mkdir(exist_ok=False)
    inv_path = BASE / "mapping_refresh_r01/node27.stdout.json"
    pci_path = BASE / "mapping_pci_followup_r01/node27.stdout.json"
    inv = json.loads(inv_path.read_text())
    assert inv["pending_exact"][0]["old_process_absent_at_sample"]
    original_path = ROOT / "codex_coding/src/v0.1.23.3/task2/three_node_recovery/inventory/fresh_host.py"
    original = original_path.read_text()
    modified = original
    changes = [
        ('spec["node"] not in ("node28","node30")', 'spec["node"] not in ("node27",)'),
        ('r"zjn-fluid-three-node-[a-z0-9_]+-n(28|30)"', 'r"zjn-fluid-three-node-multitier_admission27_r01-n27"'),
        ('r"[a-z0-9_]+_n(28|30)"', 'r"multitier_admission27_r01_n27"'),
        ('"--memory","8g"', '"--memory","6g"'),
        ('if mode=="guard":', 'if mode=="guard":\n        raise SystemExit("node27 adapter excludes driver-property guard; use read-only host_metadata instead")'),
    ]
    for old, new in changes:
        assert modified.count(old) == 1, old
        modified = modified.replace(old, new)
    compile(modified, "fresh_host27.source.py", "exec")
    (out / "fresh_host27.source.py").write_text(modified)
    (out / "fresh_host.original.py").write_text(original)
    (out / "fresh_host27.diff").write_text("".join(difflib.unified_diff(original.splitlines(True), modified.splitlines(True), fromfile="fresh_host.py", tofile="fresh_host27.py")))
    relative = "codex_coding/src/v0.1.23.3/task2/three_node_recovery/diagnosis30/init_gate_diag60.py"
    runtime_path = ROOT / relative
    runtime = runtime_path.read_bytes()
    (out / "init_gate_diag60.source.py").write_bytes(runtime)
    image = inv["containers"][0]["image_id"]
    assert image == "sha256:ee3b9807d9e90cdc3e6ab9c1058f0b78dabcafa6a0fc7661d9c5af69ce1f9588"
    spec = {"node": "node27", "ip": "12.11.3.27", "hostname": inv["hostname"], "boot_id": inv["boot_id"],
            "image_id": image, "container_name": "zjn-fluid-three-node-multitier_admission27_r01-n27",
            "stage": "/tmp/fluidckpt_three_node/multitier_admission27_r01_n27",
            "gpu": 0, "bdf": "0000:09:00.0", "host_wrapper_sha256": sha(modified.encode()),
            "protected_containers": [c["name"].lstrip("/") for c in inv["containers"]],
            "keeper_endpoints": [], "known_live_workers": [],
            "assets": [{"relative_path": relative, "sha256": sha(runtime), "base64": base64.b64encode(runtime).decode()}],
            "authorization": "prepared only; parent reviews exact source then executes setup; GPU copy is a separate action"}
    (out / "node27.setup.spec.json").write_text(json.dumps(spec, indent=2) + "\n")
    setup_argv = ["python3", str(out / "fresh_host27.source.py"), "setup", str(out / "node27.setup.spec.json")]
    runner = Path(__file__).with_name("run_admission27_copy.py")
    record = {"prepared_only": True, "gpu_started": False, "docker_created": False,
              "setup_argv": setup_argv, "setup_timeout_seconds": 35,
              "copy_entry": ["python3", str(runner), "--plan", str(out), "--setup", "<captured_setup_stdout.json>",
                             "--out", str(BASE / "mapping_admission27_run_r01"), "--execute"],
              "copy_runtime_source": relative, "copy_runtime_source_sha256": sha(runtime),
              "copy_runtime_unchanged": True, "copy_inner_deadline_seconds": 60, "copy_outer_timeout_seconds": 75,
              "payload_bytes": 1 << 20, "stage": "copy", "visibility": "selected",
              "node_dram_cap_bytes": 6 << 30, "node_hbm_cap_bytes": 2 << 30,
              "hbm_cap_enforced_by_cuda_allocator": False,
              "note": "one 1MiB tensor only; runtime physical HBM <=2GiB must be checked from actual runtime/free delta, not payload label",
              "source_refs": [{"path": str(p.relative_to(ROOT)), "sha256": sha(p.read_bytes())}
                              for p in (inv_path, pci_path, original_path, runtime_path, runner)],
              "cleanup": "unchanged successful supervisor checks boot/PIDns/PID/start/PGID/SID/argv before TERM/KILL; unresolved D leaves pending, blocks escalation",
              "after_protection": "exact fresh CID/StartedAt, original stopped-container identities unchanged, child/group empty, memory.events no OOM; no shared XML edits",
              "shared_xml_inputs": "inherited original environment; no NCCL initialization in copy stage; files never edited"}
    (out / "plan.json").write_text(json.dumps(record, indent=2) + "\n")
    (out / "setup.prepared.sh").write_text("#!/bin/bash\nset -eu\nulimit -c 0\n" + shlex.join(setup_argv) + "\n")
    (out / "report.md").write_text("# node27新隔离单卡资格准备\n\nprepared-only，未创建容器、未GPU。复用fresh_host setup，仅27身份/唯一name/stage/6GiB cap/禁止旧driver guard五处薄适配；runtime完全冻结成功init_gate_diag60.py，copy内60s、外75s，1MiB全SHA、PID精确监督和core0不变。\n\n先主审setup source及plan，执行setup命令并捕获完整stdout；再以动态CID/StartedAt运行copy_entry。runner在docker exec前后核CID/boot和旧停止容器身份，保留精确进程/结果文件。任何deadline/cleanup/pending失败阻断后续，不重启旧容器、不创建NCCL、不改原XML/defaultmapping。\n\nHBM2GiB cap仍需实际runtime增量验收；1MiB payload不代表context只占1MiB。此次单卡资格不升级六worker/网络/SSD/model资格。\n")
    print(json.dumps({"out": str(out), "prepared": True, "gpu_calls": 0, "docker_created": False}))

if __name__ == "__main__":
    main()
