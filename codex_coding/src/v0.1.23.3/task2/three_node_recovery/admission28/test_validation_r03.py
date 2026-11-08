"""Replay the real IB r02 artifacts with strict GPU/NET parsing and NIC mapping."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import runtime_gate_r02 as previous
import runtime_gate_r03 as gate

ROOT = Path(__file__).resolve().parents[6]
RESULT = ROOT / "codex_coding/results/v0.1.23.3/task2/three_node_recovery"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=RESULT / "admission28_validation_r03_cpu_r01")
    out = parser.parse_args().out.resolve()
    assert out.parent == RESULT and out.name.startswith("admission28_validation_r03_cpu_")
    out.mkdir(exist_ok=False)
    checks, replay, refs = [], [], []
    def check(name, passed):
        assert passed, name
        checks.append({"name": name, "passed": True})
    def reject_parse(name, raw):
        try:
            gate.dump_parse_view(raw)
        except (ValueError, ET.ParseError):
            check(name, True)
        else:
            raise AssertionError(name)
    old_functions = {n.name: ast.dump(n) for n in ast.parse(Path(previous.__file__).read_text()).body if isinstance(n, ast.FunctionDef)}
    new_functions = {n.name: ast.dump(n) for n in ast.parse(Path(gate.__file__).read_text()).body if isinstance(n, ast.FunctionDef)}
    unchanged = ("main", "bounded", "cleanup", "admit", "log_evidence", "gpu_paths", "dump_check", "bdf_bus_id")
    check("r02 budgets/cleanup/GPU/log bindings unchanged", all(old_functions[name] == new_functions[name] for name in unchanged))
    for node, rank, physical in (("node28", 0, 2), ("node30", 1, 0)):
        run = RESULT / ("probe_nccl_2830_" + node + "_r02")
        raw = (run / "topology.dump.xml").read_bytes()
        log = (run / "nccl.log").read_bytes()
        child = json.loads((run / "child.result.json").read_text())
        summary = json.loads((run / "summary.json").read_text())
        pid = json.loads((run / "child.owner.json").read_text())["pid"]
        view, report = gate.dump_parse_view(raw)
        check(node + " exact two GPU/NET tokens removed six bytes", report["removed_token_count"] == 2
              and {e["tag_name"] for e in report["edits"]} == {"gpu", "net"} and len(raw)-len(view) == 6
              and all(raw[e["offset"]:e["offset"]+e["length"]] == b'=""' for e in report["edits"]))
        xml = Path(child["effective_communication_environment"]["NCCL_TOPO_FILE"])
        input_raw = (RESULT / "probe_xml_2830_gdr0_r01" / (node + ".physical.gdr0.xml")).read_bytes()
        evidence = gate.log_evidence(log, pid, rank, xml, "cross", 2, 0, child["actual_bdf"], physical, required_network="IBext_v8")
        gpu = gate.dump_check(input_raw, view, child["actual_bdf"], rank, 0, evidence)
        net = gate.net_dump_check(input_raw, view, evidence)
        check(node + " actual owned IBext_v8 and HCA0 port1", evidence["passed"])
        check(node + " complete strict GPU mapping", gpu["passed"])
        check(node + " complete selected NIC PCI/NUMA/affinity/gdr0", net["passed"])
        check(node + " real full1MiB SHA and preserved XML/natural exit", child["correctness"]["passed"]
              and child["correctness"]["verified_bytes"] == 1 << 20 and child["owned_xml_preserved"] and child["original_files_preserved"]
              and summary["process"]["cleanup"]["passed"] and summary["process"]["cleanup"]["signals"] == [])
        folder = out / node
        folder.mkdir()
        (folder / "topology.parse_view.xml").write_bytes(view)
        (folder / "topology.parse_view.json").write_text(json.dumps(report, indent=2) + "\n")
        replay.append({"node": node, "parse_view": report, "communication_logs": evidence, "GPU": gpu, "NET": net,
                       "original_run_passed": False, "replayed_validation_passed": True, "new_gpu_execution": False})
        for path in (run / "topology.dump.xml", run / "nccl.log", run / "child.result.json", run / "summary.json"):
            refs.append({"path": str(path), "sha256": sha(path.read_bytes())})
        net_edit = next(e for e in report["edits"] if e["tag_name"] == "net")
        offset = net_edit["offset"]
        reject_parse(node + " anonymous NET nonempty value rejected", raw[:offset]+b'="x"'+raw[offset+3:])
        reject_parse(node + " duplicate anonymous NET rejected", raw[:offset]+b'="" =""'+raw[offset+3:])
        reject_parse(node + " NET token glued to tag name rejected", raw.replace(b"<net ", b'<net="" ', 1))
        reject_parse(node + " nonGPU nonNET anonymous token rejected", view.replace(b"<cpu ", b'<cpu ="" ', 1))
        reject_parse(node + " unrelated malformed XML rejected", raw[:-8])
        for attr, value in (("name", "shca_1"), ("port", "2"), ("gdr", "1")):
            tree = ET.fromstring(view)
            next(tree.iter("net")).set(attr, value)
            check(node + " wrong NET " + attr + " rejected", not gate.net_dump_check(input_raw, ET.tostring(tree), evidence)["passed"])
        for attr, value in (("numaid", "99"), ("affinity", "00000000,00000000,00000000,00000000")):
            tree = ET.fromstring(view)
            cpu = next(c for c in tree.findall("cpu") if c.find(".//net") is not None)
            cpu.set(attr, value)
            check(node + " wrong NET CPU " + attr + " rejected", not gate.net_dump_check(input_raw, ET.tostring(tree), evidence)["passed"])
        tree = ET.fromstring(view)
        parents = {c: p for p in tree.iter() for c in p}
        pci = parents[parents[next(tree.iter("net"))]]
        pci.set("busid", "0000:ed:00.0")
        check(node + " wrong NET PCI ancestry rejected", not gate.net_dump_check(input_raw, ET.tostring(tree), evidence)["passed"])
        socket = gate.log_evidence(log.replace(b"via NET/IBext_v8/", b"via NET/Socket/"), pid, rank, xml, "cross", 2, 0,
                                   child["actual_bdf"], physical, required_network="IBext_v8")
        check(node + " Socket cannot satisfy NET mapping", not gate.net_dump_check(input_raw, view, socket)["passed"])
        check(node + " raw artifact preserved", sha((run / "topology.dump.xml").read_bytes()) == report["raw_sha256"])
    source = Path(gate.__file__).read_bytes()
    (out / "runtime_gate_r03.source.py").write_bytes(source)
    (out / "test.source.py").write_bytes(Path(__file__).read_bytes())
    (out / "summary.json").write_text(json.dumps({"passed": True, "checks": checks, "replay": replay, "fixture_refs": refs,
                 "runtime_source_sha256": sha(source), "gpu_operations": 0, "torch_imported": "torch" in sys.modules,
                 "scope": "CPU revalidation of raw failed r02; old summaries unchanged"}, indent=2) + "\n")
    print(json.dumps({"out": str(out), "checks_passed": len(checks), "runtime_source_sha256": sha(source), "gpu_operations": 0}))


if __name__ == "__main__":
    main()
