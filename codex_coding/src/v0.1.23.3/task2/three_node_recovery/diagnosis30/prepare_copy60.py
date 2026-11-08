"""Prepare one 1MiB copy using the already deployed 60s diagnostic source."""
import hashlib
import json
from pathlib import Path
import shlex


def main():
    root = Path(__file__).resolve().parents[6]
    base = root/"codex_coding/results/v0.1.23.3/task2/three_node_recovery"
    out = base/"diagnosis30_copy60_plan_r01"
    out.mkdir(exist_ok=False)
    plan_path = base/"diagnosis30_diag60_plan_r01/launch.json"
    plan = json.loads(plan_path.read_text())
    evidence_path = base/"probe_node28_diag60_r01/summary.json"
    evidence = json.loads(evidence_path.read_text())
    assert evidence["passed"] and evidence["process"]["cleanup"]["passed"] and not evidence["process"]["timed_out"]
    assert evidence["child_result"]["actual_bdf"] == "0000:55:00.0"
    remote = plan["remote_argv"].copy()
    remote[remote.index("--stage")+1] = "copy"
    remote[remote.index("--out")+1] = str(base/"probe_node28_copy60_r01")
    argv = plan["argv"].copy()
    argv[-1] = shlex.join(remote)
    record = {"executed":False,"argv":argv,"remote_argv":remote,"run_name":"probe_node28_copy60_r01",
              "source_sha256":plan["diagnostic_source_sha256"],"source_unchanged":True,"deadline_seconds":60,"outer_timeout_seconds":75,
              "payload_bytes":1<<20,"actual_init_bdf":evidence["child_result"]["actual_bdf"],
              "actual_init_hip_free":evidence["child_result"]["hip_free_bytes"],
              "identity":"same node28 original private container, original cap/LD and thread1; father checks fresh guard",
              "source_refs":[{"path":str(p),"sha256":hashlib.sha256(p.read_bytes()).hexdigest()} for p in (plan_path,evidence_path)],
              "authorization":"parent alone executes one 1MiB round trip, saves exact artifacts, checks SHA/cleanup/after guard"}
    (out/"launch.json").write_text(json.dumps(record,indent=2)+"\n")
    (out/"command.sh").write_text("#!/bin/bash\nset -u\nulimit -c 0\n"+shlex.join(argv)+"\n")
    print(json.dumps({"out":str(out),"prepared":True,"gpu_operations":0}))


if __name__ == "__main__":
    main()
