"""Read-only launch qualification and role-aware isolated remote CLI checks."""
import argparse
import ast
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from audit_core import Audit, CALIBRATION_ORDER, CONFIGS, expected_chunks

ROOT = Path(__file__).resolve().parents[6]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--cpu", type=Path, required=True)
    parser.add_argument("--predeclared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gpu", type=int, choices=(0,4), default=0)
    args = parser.parse_args()
    args.output=args.output.resolve()
    audit = Audit()
    prepared = args.prepared.resolve()
    plan = audit.read(prepared / "launch_plan.json")
    protocol = plan["protocol"]
    audit.check("single physical node27 GPU"+str(args.gpu)+" rank0; no SSD/pair warm",
                len(plan["workers"]) == 1 and plan["workers"][0]["worker"] == 0
                and plan["workers"][0]["node"] == "node27" and plan["workers"][0]["gpu"] == args.gpu
                and plan["workers"][0]["numa"] == args.gpu
                and plan["workers"][0]["bdf"] == ("0000:09:00.0" if args.gpu==0 else "0000:85:00.0")
                and plan["ssd"] == {} and plan["necessary_pairs"] == [] and len(plan["processes"]) == 1)
    audit.check("six exact C1 requests and fixed order", plan["experiment_stage"] == "C1"
                and protocol["order"] == CALIBRATION_ORDER and len(protocol["cases"]) == 6)
    for row in protocol["cases"]:
        sizes = CONFIGS[row["id"]]
        audit.check(row["id"] + ": design matrix from op sizes", row["op_mib"] == sizes
                    and row["N_mib"] == sum(sizes) and row["K"] == len(sizes)
                    and row["expected_chunk_mib"] == expected_chunks(sizes))
    audit.check("runtime/bootstrap included aggregate 6GiB DRAM/2GiB HBM contract",
                plan["aggregate_budget"]["dram"] == 6 << 30 and plan["aggregate_budget"]["hbm"] == 2 << 30
                and protocol["bootstrap_bytes"] == 96 << 20 and plan["lifetime_s"] <= 1800)
    audit.check("old model protected and unused", plan["model_consumed"] is False)
    audit.reference(plan["frozen_model_ref"])
    prior = audit.read(prepared / "node27.prior.spec.json")
    spec = audit.read(prepared / "node27.setup.spec.json")
    old = {x["relative_path"]: x["sha256"] for x in prior["assets"]}
    now = {x["relative_path"]: x["sha256"] for x in spec["assets"]}
    audit.check("previous deployed asset versions unchanged", all(now.get(k) == v for k, v in old.items()))
    package = audit.read(prepared / "append_assets.json")
    audit.check("exact append-only new source closure", set(now)-set(old)
                == {x["relative_path"] for x in package["assets"]})
    for item in package["assets"]:
        audit.check("new asset frozen/base64/live SHA: " + item["relative_path"],
                    sha(ROOT/item["relative_path"]) == item["sha256"]
                    == hashlib.sha256(base64.b64decode(item["base64"])).hexdigest())
    for name, digest in now.items():
        audit.check("deployed source freeze: " + name, sha(ROOT/name) == digest)
    process = plan["processes"][0]
    env = process["env"]
    mapping_ref=plan.get("physical_role_mapping_ref",plan["prior_plan_ref"])
    audit.reference(mapping_ref)
    oldplan = audit.read(Path(mapping_ref["path"]))
    original = next(row for row in oldplan["processes"] if row["node"] == "node27"
                    and row.get("kind")=="worker" and row["argv"][row["argv"].index("--gpu")+1]==str(args.gpu))
    audit.check("topology input preserved and output distinct", env.get("NCCL_TOPO_FILE")
                == original["env"].get("NCCL_TOPO_FILE")
                and env.get("NCCL_TOPO_DUMP_FILE") != env.get("NCCL_TOPO_FILE"))
    pre = audit.read(args.predeclared)
    audit.check("analysis protocol exact prereg order/limits/no clamp", pre["protocol"]["calibration_order"]
                == CALIBRATION_ORDER and pre["protocol"]["fit_samples"] == CALIBRATION_ORDER[:5]
                and pre["protocol"]["max_requests"] == 8
                and pre["protocol"]["independent_limits_ns"] == {"worker_ns": 250000, "service_ns": 50000,
                    "controller_execute_ns": 1000000, "full_filled_ns": 2000000}
                and "no_clamping" in pre["protocol"]["fit"])
    for item in pre["source_refs"]:
        audit.reference(item)
    cpu = audit.read(args.cpu / "qualification.json")
    audit.check("CPU native execution qualification passed", cpu.get("passed") is True)
    audit.check("CPU qualification has no physical experiment or remote writes",
                cpu.get("GPU_launches") == 0 and cpu.get("remote_writes", 0) == 0)
    replay_spec=audit.read(args.cpu/"prepared/node27.setup.spec.json")
    for asset in replay_spec["assets"]:
        audit.check("CPU exact replay source matches launch freeze: "+asset["relative_path"],
                    now.get(asset["relative_path"])==asset["sha256"]==sha(ROOT/asset["relative_path"]))
    audit.reference(cpu["bundle_ref"])
    audit.reference(cpu["primitive_trace_ref"])
    execution_relative=Path(process["argv"][1]).relative_to(ROOT).parent
    worker_source=ROOT/execution_relative/"worker.py"
    tree=ast.parse(worker_source.read_text())
    overlay=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=="LedgerWorker")
    audit.check("overlay inherits native copy loop rather than defining _execute", "_execute" not in
        {x.name for x in overlay.body if isinstance(x,ast.FunctionDef)} and "result=super().execute(body)" in worker_source.read_text())
    audit.check("instrumentation patches native module proxy and preserves global time functions",
        "native.time=NativeClockProxy()" in worker_source.read_text() and "time.monotonic=" not in worker_source.read_text()
        and "time.monotonic_ns=" not in worker_source.read_text())
    args.output.mkdir(parents=True, exist_ok=False)
    closure = args.output / "isolated_project"
    for name in now:
        target = closure / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT/name, target)
    execution = closure / execution_relative
    commands = []
    wrapper = ("import sys,runpy; from unittest.mock import MagicMock; sys.modules['torch']=MagicMock(); "
               "p=sys.argv[1]; sys.path.insert(0,str(__import__('pathlib').Path(p).parent)); "
               "sys.argv=[p,'--help']; runpy.run_path(p,run_name='__main__')")
    proc = subprocess.run([sys.executable, "-I", "-c", wrapper, str(execution/"worker.py")],
                          cwd=closure, capture_output=True, text=True, timeout=20)
    commands.append(dict(entry="worker.py", role="remote worker", returncode=proc.returncode,
                         stdout=proc.stdout, stderr=proc.stderr))
    audit.check("isolated staged remote worker CLI imports exact frozen closure", proc.returncode == 0
                and "usage:" in proc.stdout)
    for name in ("prepare.py", "runner.py", "session.py", "cpu_check.py"):
        proc = subprocess.run([sys.executable, str(ROOT/execution_relative/name), "--help"],
                              cwd=ROOT, capture_output=True, text=True, timeout=20)
        commands.append(dict(entry=name, role="local controller", returncode=proc.returncode,
                             stdout=proc.stdout, stderr=proc.stderr))
        audit.check("local controller CLI: " + name, proc.returncode == 0 and "usage:" in proc.stdout)
    mode_path = args.output / "cost_ledger/cost_ledger_runtime_review.inventory_mode.json"
    command = [sys.executable, "-I", str(execution/"mode_file.py"), "--path", str(mode_path)]
    proc = subprocess.run(command, cwd=closure, capture_output=True, text=True, timeout=10)
    audit.check("isolated stdlib mode helper creates exact cached mode", proc.returncode == 0
                and json.loads(mode_path.read_text()) == {"mode": "cached"})
    proc2 = subprocess.run(command, cwd=closure, capture_output=True, text=True, timeout=10)
    audit.check("mode helper refuses existing file", proc2.returncode != 0)
    result = dict(passed=all(x["passed"] for x in audit.checks), checks=audit.checks, refs=audit.refs,
                  GPU_launches=0, remote_calls=0, physical_admission=False,
                  scope="CPU/source/package readiness; main must separately verify fresh physical gate and new master port")
    (args.output/"qualification.json").write_text(json.dumps(result, indent=2)+"\n")
    (args.output/"isolated_commands.json").write_text(json.dumps(commands, indent=2)+"\n")
    print(json.dumps({"passed": result["passed"], "checks": len(audit.checks),
                      "failed": [x["name"] for x in audit.checks if not x["passed"]]}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
