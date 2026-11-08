"""Register or execute one node27 two-GPU XML functional check, never node29."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import time

from common import (CONTAINER, FREE_TOLERANCE, GUARD, HERE, NODE, PAYLOAD, PROCESS, ROOT,
    catalog_hash, catalog_lease_errors, digest, free_check, input_xml, inventory, local_state, mapping_check, owned, write)
from runtime_support import http, select_base
from fluidckpt.benchmarks.topology_calibration import validate_process_exit

HOST_SNAPSHOT = ROOT / "codex_coding/src/v0.1.23.3/task2/topology_alignment/forensics/runtime_snapshot.py"
HOST_IDENTITIES = HOST_SNAPSHOT.with_name("runtime_pid_identities.py")
READ = HERE / "read_local.py"


def event(out, phase, **values):
    with (out / "driver.events.jsonl").open("a") as handle:
        handle.write(json.dumps({"phase": phase, "monotonic_ns": time.monotonic_ns(), **values}) + "\n")
        handle.flush()


def remote(state, command, seconds=30):
    # GNU timeout bounds the container process even if docker client times out.
    return json.loads(inventory.remote(state, NODE,
        ["timeout", "--signal=TERM", "--kill-after=2s", f"{seconds+5}s", *command], timeout=seconds+10))


def control_identity():
    container = subprocess.run(["docker", "inspect", "--format", "{{json .}}", CONTAINER],
                               capture_output=True, text=True, timeout=5)
    if container.returncode:
        raise ValueError("local container identity lookup failed")
    ci = json.loads(container.stdout)
    return {"id": ci["Id"], "pid": ci["State"]["Pid"], "started_at": ci["State"]["StartedAt"],
            "running": ci["State"]["Running"], "host_boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}


def log_evidence(raw, pid, rank, xml):
    lines = raw.decode(errors="replace").splitlines()
    own = [line for line in lines if re.search(rf":{pid}:\d+\s+\[{rank}\]", line)]
    loaded = [line for line in own if f"Loading topology file {xml}" in line]
    transport = [line for line in own if "via " in line and re.search(r"via (?:P2P|SHM|NET)/?", line)]
    local = [line for line in own if re.search(rf"rank {rank} nRanks 2 nNodes 1 localRanks 2 localRank {rank}\b", line)]
    fallback = [line for line in own if "Loading topology mapping file" in line]
    return {"passed": bool(loaded and transport and local and not fallback), "pid": pid, "rank": rank,
            "input_loading_lines": loaded, "transport_lines": transport, "local_communicator_lines": local,
            "fallback_mapping_lines": fallback,
            "transport_classes": sorted(set(re.findall(r"via (P2P|SHM|NET)", "\n".join(transport)))),
            "scope": "actual owned local communicator channel logs; no cross-node performance or GDR certification"}


def host_snapshot(out, stage):
    started = time.monotonic_ns()
    raw = subprocess.run(["timeout", "--signal=TERM", "--kill-after=2s", "35s", "python3", str(HOST_SNAPSHOT), CONTAINER],
                         capture_output=True, text=True, timeout=40)
    write(out / f"{stage}.host_command.json", {"returncode": raw.returncode, "stdout": raw.stdout, "stderr": raw.stderr})
    if raw.returncode:
        raise RuntimeError("bounded local host owner snapshot failed")
    value = json.loads(raw.stdout)
    value["container_identity"] = control_identity()
    if any(row["returncode"] for row in value["smi"].values()):
        raise ValueError("local SMI command failed")
    pids = sorted(set(re.findall(r"PID:\s*(\d+)", value["smi"]["pids"]["stdout"])))
    if not pids:
        raise ValueError("SMI owners absent: cannot certify fresh owner evidence")
    identities = subprocess.run(["timeout", "--signal=TERM", "--kill-after=2s", "12s", "python3", str(HOST_IDENTITIES), *pids],
                                capture_output=True, text=True, timeout=17)
    write(out / f"{stage}.owner_command.json", {"returncode": identities.returncode, "stdout": identities.stdout, "stderr": identities.stderr})
    if identities.returncode:
        raise RuntimeError("bounded local owner identity snapshot failed")
    value["smi_owner_identities"] = json.loads(identities.stdout)
    value["capture_duration_ns"] = time.monotonic_ns()-started
    write(out / f"{stage}.host.json", value)
    return value


def inspect(state, session, out, stage, port):
    event(out, f"{stage}_local_read_enter")
    with ThreadPoolExecutor(4) as pool:
        keeper_jobs = [pool.submit(http, ep, "inventory", timeout=5) for ep in state["endpoints"][:2]]
        node_job = pool.submit(remote, state, ["python3", str(READ), "--session", str(session),
                              "--out", str(out), "--snapshot", "--deadline", "30"])
        host_job = pool.submit(host_snapshot, out, stage)
        # Save each successful partial read, even if a different independent read fails.
        errors, workers, node, host = [], [], None, None
        for i, job in enumerate(keeper_jobs):
            try:
                worker = job.result()
                write(out / f"{stage}.worker_{i}.json", worker)
                workers.append(worker)
            except Exception as exc:
                errors.append(f"worker{i}: {exc!r}")
        try:
            node = node_job.result()
            write(out / f"{stage}.node.json", node)
        except Exception as exc:
            errors.append(f"node_snapshot: {exc!r}")
        try:
            host = host_job.result()
        except Exception as exc:
            errors.append(f"host_owner: {exc!r}")
    if errors:
        write(out / f"{stage}.read_failures.json", {"errors": errors, "phase": stage})
        raise RuntimeError("; ".join(errors))
    errors = []
    if [(w["worker"], w["node"], w["gpu"]) for w in workers] != [(0, NODE, 0), (1, NODE, 1)]:
        errors.append("local keeper identity mismatch")
    if len({w["session"] for w in workers}) != 1 or any(w["transactions"] for w in workers):
        errors.append("keeper mixed sessions or active transactions")
    replicas = [r for w in workers for r in w["replicas"]]
    bases = select_base(state["models"]["B"], replicas)
    for source in state["models"]["B"]["sources"]:
        actual = next(r for r in bases if r["id"] == source["id"])
        if actual["meta"] != source["meta"] or actual["index"] != source["index"]:
            errors.append("retained source metadata/index mismatch")
    if sum(r["n"] for r in bases) != 70329718112:
        errors.append("expected full 70,329,718,112B DRAM base")
    errors.extend(catalog_lease_errors(replicas, bases))
    if len(node["protected_processes"]) != 3 or not all(p["passed"] for p in node["protected_processes"]):
        errors.append("protected local spec/PID/starttime failed")
    if not node["parked_commands"] or any(not c["parked"] for c in node["parked_commands"].values()):
        errors.append("source engine is not declared parked")
    for worker in workers:
        if worker["headroom_bytes"]["hbm"] <= GUARD:
            errors.append(f"GPU{worker['gpu']} physical free does not exceed 2GiB")
        actual_gpu = next(g for g in node["topology"]["gpus"] if g["logical_gpu"] == worker["gpu"])
        if actual_gpu["numa_node"] != worker["numa"]:
            errors.append("keeper GPU/NUMA does not match fresh local discovery")
    mem = node["host_memory_bytes"].get("MemAvailable", 0)
    limit, current = node["cgroup_memory"]["max"], node["cgroup_memory"]["current"]
    if limit not in (None, "max") and current is not None:
        mem = min(mem, int(limit)-int(current))
    if mem < GUARD + (512 << 20):
        errors.append("host/cgroup headroom below 2GiB guard plus 512MiB context allowance")
    if stage == "before" and port in node["occupied_ports"]:
        errors.append("local Gloo rendezvous port occupied")
    value = {"workers": workers, "node": node, "host": host, "catalog_sha256": catalog_hash(workers),
             "base_bytes": sum(r["n"] for r in bases), "base_ids": sorted(r["id"] for r in bases),
             "resource_admission": {"passed": not errors, "errors": errors},
             "scope": "node27 only; full metadata/catalog/lease, no weight content reread",
             "performance_ready": False, "performance_blocked_reason": "shared active GPU owners; functional check only"}
    write(out / f"{stage}.json", value)
    event(out, f"{stage}_local_read_exit", passed=not errors)
    if errors:
        raise ValueError("; ".join(errors))
    return value


def specs(state, out, args, xml):
    rows = []
    for rank in (0, 1):
        path = out / f"rank_{rank}.process.json"
        env = {k: v for k, v in state.get("env", {}).items() if not k.startswith("FLUIDCKPT_")}
        env.update(PYTHONPATH=str(ROOT / "main_lib"), HIP_VISIBLE_DEVICES="0,1", CUDA_VISIBLE_DEVICES="0,1",
                   GLOO_SOCKET_IFNAME="lo", NCCL_TOPO_FILE=str(xml), NCCL_DEBUG="INFO",
                   NCCL_DEBUG_SUBSYS="INIT,GRAPH,NET", NCCL_DEBUG_FILE=str(out / f"rank_{rank}.nccl.%p.log"),
                   NCCL_TOPO_DUMP_FILE=str(out / f"rank_{rank}.topology.xml"), NCCL_TOPO_DUMP_FILE_RANK=str(rank),
                   OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
        spec = {"command": ["python3", str(HERE / "rank_entry.py"), "--out", str(out), "--rank", str(rank),
                             "--master", f"tcp://127.0.0.1:{args.port}", "--deadline", str(args.timeout)],
                "env": env, "cwd": str(ROOT), "log": str(out / f"rank_{rank}.log"), "core_dump_limit": 0,
                "ownership_namespace": out.name, "owner_node": NODE, "owner_rank": rank,
                "rendezvous": {"node": NODE, "address": "127.0.0.1", "port": args.port}}
        if Path(env["NCCL_TOPO_DUMP_FILE"]) == xml:
            raise ValueError("input and dump collision")
        write(path, spec)
        rows.append({"rank": rank, "node": NODE, "spec": str(path), "spec_sha256": digest(path)})
    return rows


def cleanup(state, out, rows, expected_container=None):
    checks = []
    for row in rows:
        if not Path(row["spec"]).with_suffix(".pid.json").exists():
            checks.append({"rank": row["rank"], "passed": True, "not_started": True})
            continue
        try:
            if expected_container is None or control_identity() != expected_container:
                raise ValueError("host boot/container continuity failed; cleanup signals rejected")
            if digest(row["spec"]) != row["spec_sha256"]:
                raise ValueError("owned spec changed")
            gate = remote(state, ["python3", str(READ), "--out", str(out), "--spec", row["spec"], "--check-stop"], 15)
            registered = gate["registered"]
            if row.get("start_reply") is not None and row["start_reply"] != registered:
                raise ValueError("launch reply differs from PID registration")
            row.setdefault("start_reply", registered)
            if gate["actual"]["live"]:
                row["stop_reply"] = remote(state, ["python3", str(PROCESS), "stop", "--spec", row["spec"]], 25)
            final = remote(state, ["python3", str(READ), "--out", str(out), "--spec", row["spec"]], 15)
            validate_process_exit(registered, final["registered"], final["actual"])
            checks.append({"rank": row["rank"], "passed": True, "evidence": final})
        except Exception as exc:
            checks.append({"rank": row["rank"], "passed": False, "error": repr(exc)})
    result = {"passed": all(c["passed"] for c in checks), "checks": checks, "scope": "exact two isolated spec/PID/starttime groups only"}
    write(out / "cleanup.json", result)
    return result


def collect(state, out, rows, xml, expected_container):
    destination = out / "postexit"
    destination.mkdir(exist_ok=False)
    checks, graphs = {}, []
    for row in rows:
        if expected_container is None or control_identity() != expected_container:
            raise ValueError("host boot/container continuity failed; postexit identities rejected")
        if not row.get("start_reply"):
            checks[f"rank_{row['rank']}_launched"] = False
            continue
        bundle = remote(state, ["python3", str(READ), "--out", str(out), "--spec", row["spec"], "--bundle"], 20)
        validate_process_exit(row["start_reply"], bundle["registered"], bundle["actual"])
        records = {}
        for artifact in bundle["artifacts"]:
            name = Path(artifact["path"]).name
            checks[name] = artifact["exists"]
            if artifact["exists"]:
                raw = base64.b64decode(artifact.pop("content_base64"), validate=True)
                saved = destination / name
                saved.write_bytes(raw)
                if hashlib.sha256(raw).hexdigest() != artifact["sha256"] or digest(out / name) != artifact["sha256"]:
                    raise ValueError("remote/local artifact SHA mismatch")
                artifact["saved_path"] = str(saved)
                records[name] = raw
        write(destination / f"rank_{row['rank']}.evidence.json", bundle)
        rank = row["rank"]
        if f"rank_{rank}.json" in records and f"rank_{rank}.environment.json" in records:
            result = json.loads(records[f"rank_{rank}.json"])
            env = json.loads(records[f"rank_{rank}.environment.json"])
            spec = json.loads(Path(row["spec"]).read_text())
            checks[f"rank_{rank}_identity_environment"] = (env["pid"] == row["start_reply"]["pid"] and
                env["process"]["start_time"] == row["start_reply"]["start_time"] and env["pre_torch_import"] and
                not env["torch_imported"] and env["core_dump_limit"] == [0, 0] and
                all(env["effective_environment"].get(k) == spec["env"][k] for k in
                    ("NCCL_TOPO_FILE", "NCCL_TOPO_DUMP_FILE", "NCCL_TOPO_DUMP_FILE_RANK", "GLOO_SOCKET_IFNAME")) and
                env["requested_topology_input"]["sha256"] == digest(xml))
            checks[f"rank_{rank}_content"] = (result["status"] == "passed" and result["reset_after_warmup"] and
                result["pid"] == row["start_reply"]["pid"] and result["process"]["start_time"] == row["start_reply"]["start_time"] and
                result["node"] == NODE and result["gpu"] == rank and result["rank"] == rank and
                result["actual_gpu_bdf"] == ("0000:09:00.0" if rank == 0 else "0000:36:00.0") and
                result["pre_transfer_sha"]["passed"] and result["pre_transfer_sha"]["expected_value"] == (31 if rank == 0 else 0) and
                result["correctness"]["passed"] and result["correctness"]["verified_bytes"] == PAYLOAD and
                result["correctness"]["sha256"] == hashlib.sha256(bytes([31])*PAYLOAD).hexdigest())
            if f"rank_{rank}.topology.xml" in records:
                graph = mapping_check(xml.read_bytes(), records[f"rank_{rank}.topology.xml"], result["actual_gpu_bdf"], rank)
                graph.update(rank=rank, sha256=hashlib.sha256(records[f"rank_{rank}.topology.xml"]).hexdigest())
                graphs.append(graph)
                checks[f"rank_{rank}_device_mapping"] = graph["passed"]
        log_name = f"rank_{rank}.nccl.{row['start_reply']['pid']}.log"
        if log_name in records:
            actual_log = log_evidence(records[log_name], row["start_reply"]["pid"], rank, xml)
            write(destination / f"rank_{rank}.transport.json", actual_log)
            checks[f"rank_{rank}_xml_loading_actual_transport"] = actual_log["passed"]
        if f"rank_{rank}.libraries.json" in records:
            libs = json.loads(records[f"rank_{rank}.libraries.json"])["libraries"]
            checks[f"rank_{rank}_communication_library_sha"] = bool(libs) and all("sha256" in lib for lib in libs)
    value = {"passed": len(graphs) == 2 and all(checks.values()), "checks": checks, "graphs": graphs,
             "scope": "two local ranks and device mapping/content only; no NIC/GDR, full-machine or performance acceptance"}
    write(destination / "summary.json", value)
    return value


def driver(args):
    xml, manifest_path, manifest = input_xml(args.xml_directory)
    state = local_state(args.session)
    out = owned(args.out)
    out.mkdir(parents=True, exist_ok=False)
    out.chmod(0o777)
    code_paths = [*HERE.glob("*.py"), Path(inventory.__file__), PROCESS, HOST_SNAPSHOT, HOST_IDENTITIES,
                  ROOT / "main_lib/fluidckpt/topology/discover.py", ROOT / "main_lib/fluidckpt/topology/rccl_xml.py",
                  ROOT / "codex_coding/src/v0.1.23.3/task2/basic_scenarios/runtime_support.py"]
    code = {str(p): digest(p) for p in sorted(code_paths)}
    inputs = {str(p): digest(p) for p in [args.session, Path(state["root_session"]), xml, manifest_path,
                                       *(Path(r["path"]) for r in manifest["source_refs"])]}
    rows = specs(state, out, args, xml)
    result = {"status": "registered_only", "out": str(out), "processes": rows,
              "code_sha256": code, "input_sha256": inputs, "performance_measurement": False,
              "runtime_accepted": False, "whole_machine_runtime_accepted": False, "nic_gdr_accepted": False,
              "payload_bytes_per_gpu": PAYLOAD, "samples_performance": 0, "only_node": NODE,
              "source_session": str(args.session), "rank_alarm_seconds": args.timeout,
              "collective_primitive": "nccl_broadcast_uint8_local_rank0_to_rank1",
              "controller_hard_rank_deadline_seconds": args.timeout+5,
              "warmup_iterations": 1, "functional_iterations": 1,
              "bytes_sent_by_source_total": 2*PAYLOAD,
              "loaded_communication_library_sha_limit_bytes": 256 << 20,
              "source_content_sha_scope": "previous validated source retained via full metadata/index/catalog identity; no weight reread"}
    write(out / "protocol.json", result)
    if not args.execute:
        write(out / "summary.json", result)
        return result
    before, expected_container = None, None
    def interrupted(signum, frame):
        raise InterruptedError("local driver signal; exact cleanup follows")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        result["status"] = "running"
        write(out / "summary.json", result)
        before = inspect(state, args.session, out, "before", args.port)
        expected_container = before["host"]["container_identity"]
        event(out, "ranks_launch_enter")
        deadline = time.monotonic()+args.timeout+5
        for row in rows:
            if control_identity() != expected_container:
                raise ValueError("host boot/container changed before own rank launch")
            row["start_reply"] = remote(state, ["python3", str(PROCESS), "start", "--spec", row["spec"]], 15)
            write(out / "summary.json", result)
        event(out, "ranks_launch_exit")
        while time.monotonic() < deadline:
            finished = []
            for row in rows:
                path = out / f"rank_{row['rank']}.json"
                data = json.loads(path.read_text()) if path.exists() else {}
                if data.get("status") == "failed":
                    raise RuntimeError(f"rank{row['rank']}: {data.get('error')}")
                finished.append(data.get("status") == "passed" and (out / f"rank_{row['rank']}.libraries.json").exists())
            if all(finished):
                break
            time.sleep(.25)
        else:
            raise TimeoutError("host hard rank deadline expired")
        result["functional_stage_complete"] = True
    except BaseException as exc:
        result.update(status="failed", error=repr(exc))
        event(out, "driver_failure", error=repr(exc))
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        result["cleanup"] = cleanup(state, out, rows, expected_container)
        try:
            result["runtime_evidence"] = collect(state, out, rows, xml, expected_container)
        except Exception as exc:
            result["runtime_evidence"] = {"passed": False, "error": repr(exc)}
        try:
            after = inspect(state, args.session, out, "after", args.port)
            if before is not None:
                def protected_identity(value):
                    keys = ("spec", "spec_sha256", "registered", "live_command_sha256", "pgid", "passed")
                    return [{k: p[k] for k in keys} for p in value["node"]["protected_processes"]]
                checks = {"catalog_metadata_epoch_lease_unchanged": before["catalog_sha256"] == after["catalog_sha256"],
                          "protected_specs_pid_starttime_argv_unchanged": protected_identity(before) == protected_identity(after),
                          "container_identity_unchanged": before["host"]["container_identity"] == after["host"]["container_identity"],
                          "host_boot_unchanged": before["host"]["boot_id"] == after["host"]["boot_id"],
                          "parked_source_commands_unchanged": before["node"]["parked_commands"] == after["node"]["parked_commands"],
                          "original_xml_unchanged": before["node"]["original_xml"] == after["node"]["original_xml"]}
                result["free_recovery"] = free_check(before["workers"], after["workers"])
                checks["physical_free_recovery"] = result["free_recovery"]["passed"]
                result["protection"] = {"passed": all(checks.values()), "checks": checks, "scope": "local source protection only"}
                drift = []
                def smi_memory(snapshot):
                    raw = snapshot["host"]["smi"]["memory_use"]["stdout"]
                    return json.loads(raw[raw.index("{"):])
                old, new = smi_memory(before), smi_memory(after)
                for card in sorted(old):
                    a, b = int(old[card]["Available memory size (MiB)"]), int(new[card]["Available memory size (MiB)"])
                    drift.append({"card": card, "before_available_mib": a, "after_available_mib": b, "delta_mib": b-a})
                result["all_card_free_drift"] = {"rows": drift, "scope": "shared physical state; drift is not attribution to probe"}
            else:
                result["protection"] = {"passed": False, "reason": "complete before snapshot unavailable"}
        except Exception as exc:
            result["protection"] = {"passed": False, "error": repr(exc)}
        result["code_preserved"] = all(digest(p) == sha for p, sha in code.items())
        result["inputs_preserved"] = all(digest(p) == sha for p, sha in inputs.items())
        passed = (result.get("functional_stage_complete", False) and result["cleanup"]["passed"] and
                  result["runtime_evidence"]["passed"] and result["protection"]["passed"] and
                  result["code_preserved"] and result["inputs_preserved"])
        result.update(status="passed" if passed else "failed", runtime_accepted=bool(passed),
                      runtime_acceptance_scope="node27 GPU0/1 local broadcast only", performance_ready=False)
        write(out / "summary.json", result)
    return result


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml-directory", type=Path, required=True)
    parser.add_argument("--session", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--port", type=int)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--emit-plan", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.emit_plan:
        if args.execute:
            parser.error("emit-plan cannot execute")
        xml, _, _ = input_xml(args.xml_directory)
        print(json.dumps({"input_xml": str(xml), "input_sha256": digest(xml), "node": NODE, "gpus": [0,1],
                          "rank_processes": 2, "gpu_operations": 0, "payload_bytes_per_rank": PAYLOAD,
                          "hbm_guard_bytes": GUARD, "unrecovered_free_tolerance_bytes": FREE_TOLERANCE,
                          "local_gloo": True, "performance_measurement": False, "runtime_accepted": False}, indent=2))
        return
    if not args.session or not args.out or not args.port or not 1024 <= args.port <= 65535 or not 1 <= args.timeout <= 180:
        parser.error("require session/new out/port and deadline 1..180s")
    args.session = args.session.resolve()
    value = driver(args)
    print(json.dumps({"status": value["status"], "out": value["out"], "performance_measurement": False}))
    if value["status"] not in ("passed", "registered_only"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
