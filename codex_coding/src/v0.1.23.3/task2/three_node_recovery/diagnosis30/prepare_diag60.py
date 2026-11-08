"""Freeze the original gate with only its non-NCCL deadline changed for one diagnostic."""
import difflib
import hashlib
import json
from pathlib import Path
import shlex


def main():
    here = Path(__file__).resolve().parent
    root = Path(__file__).resolve().parents[6]
    base = root/"codex_coding/results/v0.1.23.3/task2/three_node_recovery"
    out = base/"diagnosis30_diag60_plan_r01"
    out.mkdir(exist_ok=False)
    original_path = here.parent/"probe/init_gate.py"
    original = original_path.read_bytes()
    assert hashlib.sha256(original).hexdigest() == "927f543e2af1fee3e10c540d90ef98fa76f49f1dd242426a99fb3a188a850b9b"
    text = original.decode()
    replacements = [
        ('"hard_deadline_seconds": 45 if args.stage == "nccl" else 15,', '"hard_deadline_seconds": 45 if args.stage == "nccl" else 60,'),
        ('"init_deadline_seconds": 15, "stage": args.stage,', '"init_deadline_seconds": 15 if args.stage == "nccl" else 60, "stage": args.stage,'),
        ('process = bounded(argv, env, args.out, deadline=45 if args.stage == "nccl" else 15,', 'process = bounded(argv, env, args.out, deadline=45 if args.stage == "nccl" else 60,')]
    for old, new in replacements:
        assert text.count(old) == 1
        text = text.replace(old, new)
    new_path = here/"init_gate_diag60.py"
    assert not new_path.exists()
    compile(text, str(new_path), "exec")
    new_path.write_text(text)
    (out/"source.py").write_text(text)
    (out/"original_source.py").write_bytes(original)
    (out/"source.diff").write_text("".join(difflib.unified_diff(original.decode().splitlines(True), text.splitlines(True), fromfile="original_init_gate.py", tofile="init_gate_diag60.py")))
    baseline = json.loads((base/"admission28_observed_launch_r01/launch.json").read_text())
    remote = baseline["remote_argv"].copy()
    remote[remote.index(str(original_path))] = str(new_path)
    remote[remote.index("--out")+1] = str(base/"probe_node28_diag60_r01")
    launch = baseline["argv"].copy()
    assert launch[2] == "35s"
    launch[2] = "75s"
    launch[-1] = shlex.join(remote)
    (out/"command.sh").write_text("#!/bin/bash\nset -u\nulimit -c 0\n"+shlex.join(launch)+"\n")
    host_dir = "/tmp/fluidckpt_three_node/isolated_r02_n28/project/codex_coding/src/v0.1.23.3/task2/three_node_recovery/diagnosis30"
    opts = ["-o","BatchMode=yes","-o","ConnectTimeout=5","-o","ConnectionAttempts=1","-o","StrictHostKeyChecking=yes","-o","UpdateHostKeys=no","-o","Hostname=12.11.3.28"]
    stage = ["#!/bin/bash", "set -eu", "ulimit -c 0",
             shlex.join(["timeout","8s","ssh","-T",*opts,"node28","mkdir -p "+shlex.quote(host_dir)]),
             shlex.join(["timeout","8s","scp",*opts,str(out/"source.py"),"node28:"+host_dir+"/init_gate_diag60.py"]),
             shlex.join(["timeout","8s","ssh","-T",*opts,"node28","sha256sum "+shlex.quote(host_dir+"/init_gate_diag60.py")])]
    (out/"stage.sh").write_text("\n".join(stage)+"\n")
    record = {"prepared_gpu_executed":False,"run_name":"probe_node28_diag60_r01","single_diagnostic_repetitions":1,
              "original_source_sha256":hashlib.sha256(original).hexdigest(),"diagnostic_source_sha256":hashlib.sha256(text.encode()).hexdigest(),
              "source_delta":"three deadline record/call lines only; exact cleanup/worker body unchanged",
              "local_source":str(new_path),"argv":launch,"remote_argv":remote,"deadline_seconds":60,"outer_timeout_seconds":75,
              "container_id":baseline["exact_container_id"],"container_started":baseline["container_started"],
              "GPU":2,"BDF":"0000:55:00.0","IPC":"original private","LD_LIBRARY_PATH":"original inherited; no added path",
              "capabilities":"original IPC_LOCK only; no SYS_NICE","threads":{"OMP_NUM_THREADS":"1","MKL_NUM_THREADS":"1"},
              "guard_ref":str(base/"admission28_guard_before_diag60_r01/stdout.json"),
              "reason":"exact observer showed increasing CPU ticks and resident pages; 15s may truncate work, while log/cap diagnosis did not restore 15s init",
              "interpretation":"one 60s timing-boundary diagnostic, zero payload; success does not overwrite or certify original 15s admission",
              "cleanup":"unchanged original PID/start/PGID/SID/boot/PIDns supervisor; parent confirms exit and after guard including memory.events",
              "source_script_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (out/"launch.json").write_text(json.dumps(record,indent=2)+"\n")
    print(json.dumps({"out":str(out),"diagnostic_source_sha256":record["diagnostic_source_sha256"],"gpu_operations":0}))


if __name__ == "__main__":
    main()
