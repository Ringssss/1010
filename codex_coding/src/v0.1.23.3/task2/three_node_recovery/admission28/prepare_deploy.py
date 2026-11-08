"""Freeze exact two-rank NCCL deployment bundles and parent native commands."""
import base64
import hashlib
import json
from pathlib import Path
import shlex

ROOT = Path(__file__).resolve().parents[6]
REL_RESULT = Path("codex_coding/results/v0.1.23.3/task2/three_node_recovery")
REL_GATE = Path("codex_coding/src/v0.1.23.3/task2/three_node_recovery/admission28/runtime_gate.py")
NODES = (
    ("node28", str(ROOT), "e693e946828a37dbf4daa3bc25855b1c28f88bd02cdd93ce0d2e95f33e37d3ba", "2026-10-09T06:19:44.292587393Z", "m03r2n08", "5313e6f8-1712-45dc-9855-4c47358b6c4b", 2, "0000:55:00.0", 0),
    ("node30", "/dev/shm/fluidckpt_diag30_r01/project", "7d3143da42c4c16a9f48e61189035e48a2dc3d9f177cd6207cb9b7f447120695", "2026-10-08T08:19:06.916290051Z", "m03r2n10", "d9d6a6fb-013f-49ac-98f9-e77eca67952e", 0, "0000:09:00.0", 1),
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def asset(relative, raw):
    return {"relative_path": str(relative), "sha256": sha(raw), "base64": base64.b64encode(raw).decode(), "bytes": len(raw)}


def main():
    result = ROOT / REL_RESULT
    cpu_xml = result / "probe_xml_2830_gdr0_r01"
    out = result / "admission28_nccl_deploy_r01"
    out.mkdir(exist_ok=False)
    gate_raw = (ROOT / REL_GATE).read_bytes()
    assert sha(gate_raw) == "97a740672a2c1e526ecdc80ae4592c7438ff7edddc3c81a60ab120c28feaef11"
    references = []
    for node, deployed_root, cid, started, hostname, boot, gpu, bdf, rank in NODES:
        root = Path(deployed_root)
        validation_path = cpu_xml / (node + ".validation.json")
        assert json.loads(validation_path.read_text())["passed"]
        net_path = result / "admission28_nccl_plan_r01" / (node + ".network.stdout.txt")
        network = json.loads(net_path.read_text())
        assert network["passed"] and network["socket_interface"] == "ib0"
        xml_path = cpu_xml / (node + ".physical.gdr0.xml")
        xml_raw = xml_path.read_bytes()
        manifest_path = cpu_xml / (node + ".manifest.json")
        original_raw = manifest_path.read_bytes()
        manifest = json.loads(original_raw)
        assert manifest["sha256"] == sha(xml_raw) and manifest["node"] == node
        xml_relative = REL_RESULT / "probe_xml_2830_gdr0_r01" / xml_path.name
        manifest_relative = REL_RESULT / "probe_xml_2830_gdr0_r01" / manifest_path.name
        manifest["input_file"] = str(root / xml_relative)
        deployed_raw = original_raw if node == "node28" else (json.dumps(manifest, indent=2) + "\n").encode()
        if node == "node30":
            check = json.loads(deployed_raw)
            check["input_file"] = json.loads(original_raw)["input_file"]
            assert check == json.loads(original_raw)
        (out / (node + ".deployment.manifest.json")).write_bytes(deployed_raw)
        bundle = {"node": node, "root": str(root), "assets": [asset(REL_GATE, gate_raw), asset(xml_relative, xml_raw), asset(manifest_relative, deployed_raw)],
                  "container_id": cid, "container_started": started, "hostname": hostname, "boot_id": boot,
                  "cpu_manifest_path": str(manifest_path), "cpu_manifest_sha256": sha(original_raw),
                  "deployment_manifest_sha256": sha(deployed_raw), "xml_bytes_preserved": True,
                  "cpu_validation_sha256": sha(validation_path.read_bytes()), "network_evidence_sha256": sha(net_path.read_bytes()),
                  "stage_scope": "exact three owned files; no overwrite unless existing bytes already have expected SHA", "gpu_operations": 0}
        bundle_path = out / (node + ".bundle.json")
        bundle_path.write_text(json.dumps(bundle, indent=2) + "\n")
        run_name = "probe_nccl_2830_" + node + "_r01"
        remote = ["docker", "exec", "-e", "OMP_NUM_THREADS=1", "-e", "MKL_NUM_THREADS=1", "-e", "PYTHONDONTWRITEBYTECODE=1",
                  "-e", "NCCL_SOCKET_IFNAME=ib0", "-e", "NCCL_IB_HCA==shca_0:1", cid, "python3", str(root / REL_GATE),
                  "--node", node, "--container-id", cid, "--container-started-at", started,
                  "--host-boot-id", boot, "--expected-container-hostname", hostname, "--gpu", str(gpu), "--bdf", bdf,
                  "--visibility", "selected", "--stage", "nccl", "--rank", str(rank), "--world-size", "2",
                  "--master", "tcp://12.11.3.28:29870", "--xml-manifest", str(root / manifest_relative), "--scope", "cross",
                  "--out", str(root / REL_RESULT / run_name)]
        outer = ["timeout", "--signal=TERM", "100s", "ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                 "-o", "ConnectionAttempts=1", "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no",
                 "-o", "ServerAliveInterval=3", "-o", "ServerAliveCountMax=1", "-o", "Hostname=12.11.3." + node[4:], node, shlex.join(remote)]
        command = "#!/bin/bash\nset -u\nulimit -c 0\n" + shlex.join(outer) + "\n"
        (out / (node + ".command.sh")).write_text(command)
        references.append({"node": node, "rank": rank, "root": str(root), "container_id": cid, "container_started": started,
                           "bundle": str(bundle_path), "bundle_sha256": sha(bundle_path.read_bytes()),
                           "command_sha256": sha(command.encode()), "outer_argv": outer, "remote_argv": remote,
                           "run_name": run_name, "manifest_relative_path": str(manifest_relative),
                           "parent_preconditions": "both init/copy passed, fresh guards and staged SHA; parent parallel launch", "executed": False})
    (out / "launches.json").write_text(json.dumps({"ranks": references, "NCCL_IB_HCA": "=shca_0:1", "NCCL_SOCKET_IFNAME": "ib0",
                                                  "init_seconds": 60, "total_seconds": 90, "outer_seconds": 100,
                                                  "communicator_seconds": 20, "gpu_operations": 0}, indent=2) + "\n")
    print(json.dumps({"out": str(out), "bundles": [r["bundle"] for r in references], "gpu_operations": 0}))


if __name__ == "__main__":
    main()
