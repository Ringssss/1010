"""Replay original two-rank artifacts and reject damaged identities/mappings on CPU."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

import runtime_gate_r02 as gate

ROOT = Path(__file__).resolve().parents[6]
RESULT = ROOT / "codex_coding/results/v0.1.23.3/task2/three_node_recovery"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    out = RESULT / "admission28_validation_r02_cpu_r01"
    out.mkdir(exist_ok=False)
    checks, fixtures, replay = [], [], []

    def check(name, passed):
        if not passed:
            raise AssertionError(name)
        checks.append({"name": name, "passed": True})

    def rejects_parse(name, raw):
        try:
            gate.dump_parse_view(raw)
        except (ValueError, ET.ParseError):
            check(name, True)
        else:
            raise AssertionError(name)

    for node, rank, physical in (("node28", 0, 2), ("node30", 1, 0)):
        run = RESULT / ("probe_nccl_2830_" + node + "_r01")
        dump_path, log_path = run / "topology.dump.xml", run / "nccl.log"
        dump, log = dump_path.read_bytes(), log_path.read_bytes()
        owner = json.loads((run / "child.owner.json").read_text())
        child = json.loads((run / "child.result.json").read_text())
        bdf, pid = child["actual_bdf"], owner["pid"]
        xml = Path(child["effective_communication_environment"]["NCCL_TOPO_FILE"])
        input_raw = (RESULT / "probe_xml_2830_gdr0_r01" / (node + ".physical.gdr0.xml")).read_bytes()
        view, report = gate.dump_parse_view(dump)
        evidence = gate.log_evidence(log, pid, rank, xml, "cross", 2, 0, bdf, physical)
        mapping = gate.dump_check(input_raw, view, bdf, rank, 0, evidence)
        check(node + " original malformed dump resolves one exact token", report["removed_token_count"] == 1 and len(dump)-len(view) == 3)
        check(node + " actual PID/CUDA/global-rank/NVML/BDF communicator binding", evidence["passed"])
        check(node + " complete physical mapping with dumpdev=loggedNVML", mapping["passed"])
        check(node + " original full1MiB SHA and natural clean exit", child["correctness"]["passed"] and child["correctness"]["verified_bytes"] == 1 << 20
              and json.loads((run / "summary.json").read_text())["process"]["cleanup"]["signals"] == [])
        derived = out / node
        derived.mkdir()
        (derived / "topology.parse_view.xml").write_bytes(view)
        (derived / "topology.parse_view.json").write_text(json.dumps(report, indent=2) + "\n")
        replay.append({"node": node, "device_binding": evidence["device_binding"], "mapping": mapping,
                       "communication_logs": evidence, "parse_view": report, "original_run_passed": False,
                       "replayed_validation_passed": True, "transport": "NET/Socket", "new_gpu_execution": False})
        for path in (dump_path, log_path, run / "child.result.json", run / "summary.json"):
            fixtures.append({"path": str(path), "sha256": sha(path.read_bytes())})
        rejects_parse(node + " anonymous nonempty value rejected", dump.replace(b'=""', b'="x"', 1))
        rejects_parse(node + " nonGPU anonymous token rejected", view.replace(b"<cpu ", b'<cpu ="" ', 1))
        rejects_parse(node + " second anonymous GPU token rejected", dump.replace(b'=""', b'="" =""', 1))
        rejects_parse(node + " other malformed XML rejected", dump[:-8])
        valid_named = view.replace(b"<gpu ", b'<gpu note="" ', 1)
        named_view, named_report = gate.dump_parse_view(valid_named)
        check(node + " named empty value is preserved", named_view == valid_named and named_report["removed_token_count"] == 0)
        quoted = dump.replace(b"<gpu ", b'<gpu note=\' =""\' ', 1)
        quoted_view, quoted_report = gate.dump_parse_view(quoted)
        check(node + " anonymous-looking text inside value is preserved", quoted_report["removed_token_count"] == 1 and b'note=\' =""\'' in quoted_view)
        log_changes = (
            ("wrong PID", log.replace(f":{pid}:".encode(), f":{pid+1000}:".encode())),
            ("wrong CUDA prefix", log.replace(b" [0]", b" [1]")),
            ("wrong global rank", re.sub(rb"rank " + str(rank).encode() + rb"(?=\s)", b"rank " + str(1-rank).encode(), log)),
            ("wrong cudaDev identity", log.replace(b"cudaDev 0", b"cudaDev 1")),
            ("wrong nvmlDev identity", log.replace(b"nvmlDev " + str(physical).encode(), b"nvmlDev 7")),
            ("wrong busId identity", log.replace(b"busId " + format(gate.bdf_bus_id(bdf), "x").encode(), b"busId dead0")),
            ("wrong world size", log.replace(b"nranks 2", b"nranks 3").replace(b"nRanks 2", b"nRanks 3")),
            ("cross without NET", log.replace(b"via NET/", b"via SHM/")),
            ("cross wrong node count", log.replace(b"nNodes 2", b"nNodes 1")),
            ("wrong XML input path", log.replace(str(xml).encode(), (str(xml) + ".other").encode())),
            ("mapping fallback", log + f"m:{pid}:1 [0] NCCL INFO Loading topology mapping file /default.xml\n".encode()),
        )
        for label, modified in log_changes:
            check(node + " " + label + " rejected", not gate.log_evidence(modified, pid, rank, xml, "cross", 2, 0, bdf, physical)["passed"])
        for attr, wrong in (("dev", "7"), ("rank", str(1-rank)), ("gdr", "1")):
            tree = ET.fromstring(view)
            target = next(e for e in tree.iter("pci") if e.get("busid") == bdf)
            target.find("gpu").set(attr, wrong)
            check(node + " wrong dump " + attr + " rejected", not gate.dump_check(input_raw, ET.tostring(tree), bdf, rank, 0, evidence)["passed"])
        for attr, wrong in (("numaid", "99"), ("affinity", "00000000,00000000,00000000,00000000")):
            tree = ET.fromstring(view)
            cpu = next(e for e in tree.findall("cpu") if any(p.get("busid") == bdf for p in e.iter("pci")))
            cpu.set(attr, wrong)
            check(node + " wrong dump CPU " + attr + " rejected", not gate.dump_check(input_raw, ET.tostring(tree), bdf, rank, 0, evidence)["passed"])
        tree = ET.fromstring(view)
        target = next(e for e in tree.iter("pci") if e.get("busid") == bdf)
        target.set("busid", "0000:ee:00.0")
        check(node + " wrong selected BDF rejected", not gate.dump_check(input_raw, ET.tostring(tree), bdf, rank, 0, evidence)["passed"])
        check(node + " raw dump unchanged after CPU replay", sha(dump_path.read_bytes()) == report["raw_sha256"])

    source_raw = Path(gate.__file__).read_bytes()
    tree = ast.parse(source_raw)
    main_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    registration = next(n.value for n in ast.walk(main_fn) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "registration" for t in n.targets))
    fields = {k.value: v for k, v in zip(registration.keys, registration.values()) if isinstance(k, ast.Constant)}
    bounded_call = next(n for n in ast.walk(main_fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "bounded")
    budget = {k.arg: k.value for k in bounded_call.keywords}
    for stage, hard, init in (("init", 60, None), ("copy", 60, None), ("nccl", 90, 60)):
        args = type("Arguments", (), {"stage": stage})()
        evaluate = lambda value: eval(compile(ast.Expression(value), "budget_contract", "eval"), {"args": args})
        check(stage + " declared and executed budget agree", evaluate(fields["hard_deadline_seconds"]) == evaluate(budget["deadline"]) == hard
              and evaluate(fields["init_deadline_seconds"]) == 60 and evaluate(budget["init_seconds"]) == init)
    fixture = str(ROOT / "codex_coding/src/v0.1.23.3/task2/three_node_recovery/probe/cpu_fixture.py")
    for label, init_delay, total, init_limit, expected_timeout in (("init_before_deadline", 0.02, 1, 0.2, False),
              ("init_late_rejected", 0.3, 1, 0.15, True), ("total_deadline_after_init", 0.02, 0.15, 0.1, True)):
        case = out / label
        case.mkdir()
        argv = [sys.executable, fixture, "--init-event", str(case / "child.events.jsonl"), "--init-delay", str(init_delay), "--delay", "0.3"]
        result = gate.bounded(argv, dict(os.environ), case, deadline=total, init_seconds=init_limit)
        check(label + " bounded exact cleanup", result["timed_out"] == expected_timeout and result["cleanup"]["passed"] and not result["actual_after"]["live"])
    summary = {"passed": True, "checks": checks, "fixture_refs": fixtures, "replay": replay,
               "runtime_source_sha256": sha(source_raw), "gpu_operations": 0, "torch_imported": "torch" in sys.modules,
               "scope": "CPU revalidation of frozen r01 evidence; original failed summaries remain false; no new GPU run"}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out / "runtime_gate_r02.source.py").write_bytes(source_raw)
    (out / "test.source.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"out": str(out), "checks_passed": len(checks), "runtime_source_sha256": sha(source_raw), "gpu_operations": 0}))


if __name__ == "__main__":
    main()
