"""Replace only the exited target engine, verify full restore, and publish a parked overlay."""
import argparse
import copy
import json
from pathlib import Path
import resource
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "main_lib"))
sys.path.insert(0, str(ROOT / "codex_coding/src/v0.1.23.3/task2/basic_scenarios"))
from runtime_support import generate, http, load_state, retained_inventory, select_base, source_signature, target
from fluidckpt.benchmarks.serverless_adapter import atomic_json
from fluidckpt.cluster.control import Cluster
from fluidckpt.serving.handoff import wait_for_exports
from full_protocol import frozen_rank_hashes


def no_core():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def finish_then_snapshot(cluster, flow, state):
    """Release this coordinator's leases before a no-active-transactions inventory gate."""
    cluster.finish(flow["tx"])
    flow["transaction_finished"] = True
    return retained_inventory(state)


def main():
    no_core()
    p = argparse.ArgumentParser()
    p.add_argument("--session", type=Path, required=True)
    p.add_argument("--template-spec", type=Path, required=True)
    p.add_argument("--run", required=True)
    p.add_argument("--handoff-timeout", type=int, default=900)
    p.add_argument("--frozen-source", type=Path, default=ROOT / "codex_coding/results/v0.1.23.3/task2/basic_scenarios/s2_r01/summary.json")
    a = p.parse_args()
    if not a.run.startswith("serverless_") or "/" in a.run or a.handoff_timeout < 600:
        raise ValueError("use one fresh serverless run and a handoff timeout of at least 600s")
    out = ROOT / "codex_coding/results/v0.1.23.3/task2/baseline_loading" / a.run
    out.mkdir(parents=True, exist_ok=False)
    state = load_state(a.session)
    model = state["models"]["B"]
    slot = state["slots"]["target_b"]
    node = "node29"
    container = state["nodes"][node]["container"]
    if slot["endpoint"] != f"http://{state['nodes'][node]['ip']}:33340" or slot["workers"] != [2, 3]:
        raise ValueError("recovery is restricted to the exited node29 TP2 target fixture")
    expected = frozen_rank_hashes(a.frozen_source, model)
    template = json.loads(a.template_spec.read_text())
    phase = a.run + "_bootstrap"
    metrics = out / "metrics" / phase
    new_command = out / "engine.command.json"
    new_spec = out / "fresh_engine_node29.process.json"
    process_tool = ROOT / "codex_coding/src/task2/cluster/process.py"
    result = {"status": "running", "scope": "target-only functional recovery; not a Store performance sample",
              "commands": [], "owned_process_spec": str(new_spec), "node": node, "container": container}
    cluster = None
    flow = None
    started = False
    keep = False
    beginning = time.monotonic()

    def save():
        atomic_json(out / "summary.json", result)

    def exec_target(command, timeout=120, check=True):
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", node,
                shlex.join(["docker", "exec", container, *command])]
        reply = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, preexec_fn=no_core)
        result["commands"].append({"argv": argv, "returncode": reply.returncode, "stdout": reply.stdout, "stderr": reply.stderr})
        save()
        if check and reply.returncode:
            raise RuntimeError("target-only command failed; inspect summary command evidence")
        return reply.stdout

    try:
        before, replicas = retained_inventory(state)
        select_base(model, replicas)
        signature = source_signature(replicas)
        result["protected_before"] = signature
        result["inventory_before"] = before
        leases_before = {row["id"]: row.get("leases", 0) for row in replicas}
        result["leases_before"] = leases_before
        # Refuse to replace a still-running engine, even if its HTTP listener is absent.
        old_record = json.loads(a.template_spec.with_suffix(".pid.json").read_text())
        old_stat = exec_target(["cat", f"/proc/{old_record['pid']}/stat"], check=False).strip()
        if old_stat:
            fields = old_stat.rsplit(")", 1)[1].split()
            if fields[19] != old_record["start_time"] or fields[0] != "Z":
                raise RuntimeError("old target PID is alive or reused; recovery will not signal it")
        result["old_engine_record"] = old_record
        for source, worker in zip(model["sources"], slot["workers"]):
            if before[worker]["headroom_bytes"]["hbm"] < source["n"] + (2 << 30):
                raise MemoryError("target-only recovery lacks complete physical HBM headroom")
        env = dict(template["env"], FLUIDCKPT_TIMEOUT=str(a.handoff_timeout), FLUIDCKPT_TAG=phase,
                   FLUIDCKPT_METRICS=str(metrics), FLUIDCKPT_LIFECYCLE=str(new_command))
        # Every other runtime option and environment value is copied unchanged.
        spec = dict(template, env=env, log=str(out / "fresh_engine_node29.log"))
        atomic_json(new_command, {"tag": phase, "mode": "restore", "metrics": str(metrics)})
        atomic_json(new_spec, spec)
        result["runtime_env_changes"] = {key: {"before": template["env"].get(key), "after": value}
            for key, value in env.items() if template["env"].get(key) != value}
        result["start_reply"] = json.loads(exec_target(["python3", str(process_tool), "start", "--spec", str(new_spec)]))
        started = True
        directory = Path(state["handoff"]) / phase
        result["handoff_wait"] = wait_for_exports(directory, 2, timeout=300, poll_s=.05)
        targets = [target(source, f"{phase}/rank{source['tp']}", slot["workers"][source["tp"]],
                          mode="live", tag=phase, metrics_directory=str(metrics)) for source in model["sources"]]
        cluster = Cluster(state["endpoints"], state["profile"], state["endpoints"][0])
        plan = cluster.plan_restore(targets, "resource_lp", model["source_ids"])
        flow = cluster.transfer(plan, verify_before_ready=True, retain=True)
        result["functional_restore"] = flow
        verification = flow["verification"]
        if not verification["passed"] or verification["checked_bytes"] != 70329718112:
            raise RuntimeError("functional recovery did not check every logical byte")
        for op in flow["plan"]["ops"]:
            rows = verification["operations"]
            observed = rows[op["op_id"]] if op["op_id"] in rows else rows[str(op["op_id"])]
            if (op["off"] != 0 or op["n"] != model["sources"][op["tp"]]["n"]
                    or observed["target_sha256"] != expected[op["tp"]]):
                raise RuntimeError("recovery does not match the frozen full-rank S2 SHA")
        deadline = time.monotonic()+300
        while True:
            try:
                result["model_info"] = http(slot["endpoint"], "get_model_info")
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise TimeoutError("fresh target did not finish startup")
                time.sleep(.5)
        reply = generate(state, slot)
        result["reply"] = reply
        result["text_correct"] = reply["text"] == model["reference"]["text"]
        if not result["text_correct"]:
            raise RuntimeError("fresh target reference generation mismatch")
        ready_workers, _ = finish_then_snapshot(cluster, flow, state)
        result["inventory_target_ready"] = ready_workers
        flow = None
        result["release_ack"] = http(slot["endpoint"], "release_memory_occupation", {"tags": ["weights"]}, timeout=300)
        release_rows = {}
        for rank in range(2):
            events = [json.loads(line) for line in (metrics / f"rank{rank}.jsonl").read_text().splitlines()]
            begin_rows = [row for row in events if row["event"] == "release_begin"]
            end_rows = [row for row in events if row["event"] == "release_end"]
            if len(begin_rows) != 1 or len(end_rows) != 1:
                raise ValueError("functional recovery release metrics are incomplete")
            release_rows[str(rank)] = {"pre_release_engine_allocated_bytes": begin_rows[0]["allocated_bytes"],
                "parked_engine_allocated_bytes": end_rows[0]["allocated_bytes"],
                "post_release_physical_free_bytes": end_rows[0]["free_bytes"],
                "release_begin": begin_rows[0], "release_end": end_rows[0]}
        result["engine_release_memory"] = release_rows
        after, after_replicas = retained_inventory(state)
        result["inventory_after"] = after
        result["protected_metadata_unchanged"] = source_signature(after_replicas) == signature
        result["leases_after"] = {row["id"]: row.get("leases", 0) for row in after_replicas}
        result["leases_unchanged"] = result["leases_after"] == leases_before
        result["no_active_transactions"] = all(not worker["transactions"] for worker in after)
        result["physical_weight_reclaimed_bytes"] = {str(w): after[w]["headroom_bytes"]["hbm"]-ready_workers[w]["headroom_bytes"]["hbm"] for w in slot["workers"]}
        result["physical_hbm_reclaimed"] = all(result["physical_weight_reclaimed_bytes"][str(w)] >= model["sources"][rank]["n"]-(256 << 20) for rank, w in enumerate(slot["workers"]))
        result["reclaim_scope"] = "complete weight bytes released; freshly initialized engine KV/context remain parked"
        if not result["protected_metadata_unchanged"] or not result["physical_hbm_reclaimed"] or not result["leases_unchanged"] or not result["no_active_transactions"]:
            raise RuntimeError("recovery fixture protection or physical reclaim failed")
        overlay = copy.deepcopy(state)
        overlay["slots"]["target_b"].update(command=str(new_command), metrics=str(metrics), parked=True,
            ownership_run=str(out), process_spec=str(new_spec), recovery_parent_session=str(a.session.resolve()))
        overlay["recovery_parent_session"] = str(a.session.resolve())
        overlay["target_recovery_run"] = str(out)
        atomic_json(out / "session_overlay.json", overlay)
        result["session_overlay"] = str(out / "session_overlay.json")
        result["functional_setup_s"] = time.monotonic()-beginning
        result["status"] = "passed"
        keep = True
    except BaseException as error:
        result["status"] = "failed"
        result["error"] = repr(error)
        raise
    finally:
        if cluster is not None and flow is not None and not flow.get("transaction_finished"):
            try:
                cluster.finish(flow["tx"])
            except Exception as error:
                result["transaction_cleanup_error"] = repr(error)
        if started and not keep:
            try:
                result["owned_engine_stop"] = exec_target(["python3", str(process_tool), "stop", "--spec", str(new_spec)], timeout=45)
            except Exception as error:
                result["owned_engine_stop_error"] = repr(error)
        save()
    print(json.dumps({"status": result["status"], "overlay": result["session_overlay"]}))


if __name__ == "__main__":
    main()
