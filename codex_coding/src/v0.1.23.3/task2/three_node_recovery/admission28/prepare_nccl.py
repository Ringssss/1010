"""Prepare two conservative physical XML seeds; real namespace validation is pending."""
import ast
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.topology.rccl_xml import physical_xml


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    result = ROOT / "codex_coding/results/v0.1.23.3/task2/three_node_recovery"
    out = result / "admission28_nccl_plan_r01"
    out.mkdir(exist_ok=False)
    gate_path = Path(__file__).with_name("runtime_gate.py")
    gate_raw = gate_path.read_bytes()
    ast.parse(gate_raw)
    (out / "runtime_gate.source.py").write_bytes(gate_raw)
    rows = []
    for node, gpu, bdf in (("node28", 2, "0000:55:00.0"), ("node30", 0, "0000:09:00.0")):
        host_path = result / "inventory_r03" / (node + ".stdout.json")
        host_raw = host_path.read_bytes()
        host = json.loads(host_raw)
        raw, policy = physical_xml(host)
        parsed = ET.fromstring(raw)
        assert len(list(parsed.iter("gpu"))) == 8 and len(list(parsed.iter("nic"))) == 4
        assert all(e.get("gdr") == "0" for e in [*parsed.iter("gpu"), *parsed.iter("net")])
        assert any(e.get("busid") == bdf for e in parsed.iter("pci"))
        assert all(not ({"rank", "dev", "guid"} & set(e.attrib)) for e in parsed.iter())
        destination = out / (node + ".physical.gdr0.xml")
        destination.write_bytes(raw)
        policy.update(node=node, hostname=host["hostname"], boot_id=host["boot_id"],
                      input_file=str(result / "probe_xml_2830_gdr0_r01" / destination.name),
                      local_candidate_file=str(destination), namespace_validation_passed=False,
                      stage="offline seed only; parent collects real container namespace then validates",
                      source_refs=[{"path": str(host_path), "sha256": sha(host_raw)}])
        (out / (node + ".candidate.manifest.json")).write_text(json.dumps(policy, indent=2) + "\n")
        rows.append({"node": node, "gpu": gpu, "bdf": bdf, "hostname": host["hostname"],
                     "boot_id": host["boot_id"], "xml_sha256": sha(raw), "namespace_pending": True,
                     "host_path": str(host_path), "host_sha256": sha(host_raw)})
    summary = {"nodes": rows, "runtime_gate_sha256": sha(gate_raw),
               "old_gate_sha256": "927f543e2af1fee3e10c540d90ef98fa76f49f1dd242426a99fb3a188a850b9b",
               "nccl_init_deadline_seconds": 60, "nccl_total_deadline_seconds": 90,
               "communicator_timeout_seconds": 20, "other_stage_deadline_seconds": 15,
               "payload_bytes": 1 << 20, "expected_world_size": 2, "expected_scope": "cross",
               "gdr_policy": 0, "runtime_accepted": False, "performance_accepted": False,
               "remote_operations": 0, "gpu_operations": 0,
               "builder_source_sha256": sha((ROOT / "main_lib/fluidckpt/topology/rccl_xml.py").read_bytes())}
    (out / "plan.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"out": str(out), "offline_seed_ready": True, "namespace_pending": True}))


if __name__ == "__main__":
    main()
