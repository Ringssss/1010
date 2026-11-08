"""Prepare the first node30 1MiB copy in its already-staged private shm project."""
import hashlib
import json
from pathlib import Path
import shlex


def main():
    base=Path(__file__).resolve().parents[6]/"codex_coding/results/v0.1.23.3/task2/three_node_recovery"
    out=base/"diagnosis30_copy30_plan_r01"
    out.mkdir(exist_ok=False)
    plan_path=base/"diagnosis30_shm60_stage_r01/launch.json"
    evidence_path=base/"probe_node30_diag60_r01/summary.json"
    plan=json.loads(plan_path.read_text())
    evidence=json.loads(evidence_path.read_text())
    assert evidence["passed"] and evidence["process"]["cleanup"]["passed"] and not evidence["process"]["timed_out"]
    assert evidence["child_result"]["actual_bdf"]=="0000:09:00.0"
    remote=plan["remote_argv"].copy()
    remote[remote.index("--stage")+1]="copy"
    remote[remote.index("--out")+1]=plan["out_in_container"].rsplit("/",1)[0]+"/probe_node30_copy60_r01"
    assert remote[-1].startswith("/dev/shm/fluidckpt_diag30_r01/project/")
    argv=plan["argv"].copy()
    argv[-1]=shlex.join(remote)
    record={"executed_gpu":False,"argv":argv,"remote_argv":remote,"run_name":"probe_node30_copy60_r01",
            "source_sha256":plan["source_sha256"],"source_unchanged":True,"deadline_seconds":60,"outer_timeout_seconds":75,
            "out_in_container":remote[-1],"payload_bytes":1048576,"actual_init_bdf":evidence["child_result"]["actual_bdf"],
            "actual_init_hip_free":evidence["child_result"]["hip_free_bytes"],
            "guard_ref":str(base/"diagnosis30_guard_reused_after_init_r01/stdout.json"),
            "same_configuration":"existing node30 private container/shm project, original cap/LD/GPU0/selected/thread1; only stage copy and unique output",
            "source_refs":[{"path":str(p),"sha256":hashlib.sha256(p.read_bytes()).hexdigest()} for p in (plan_path,evidence_path)],
            "authorization":"parent executes and checks full 1MiB SHA, exact exit and after protection; functional only"}
    (out/"launch.json").write_text(json.dumps(record,indent=2)+"\n")
    (out/"command.sh").write_text("#!/bin/bash\nset -u\nulimit -c 0\n"+shlex.join(argv)+"\n")
    print(json.dumps({"out":str(out),"prepared":True,"gpu_operations":0}))


if __name__=="__main__":main()
