"""Opt-in, bounded XML-versus-unset probes around the frozen calibration runner."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import resource
import sys
import time
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
DIAGNOSIS = HERE.parents[1] / "topology_scenarios/diagnosis"
sys.path.insert(0, str(DIAGNOSIS))
import run_diagnosis as previous
from fluidckpt.benchmarks.topology_calibration import shared_gate, validate_process_exit

base = previous.original
inventory = previous.inventory
RESULT_ROOT = inventory.ROOT / "codex_coding/results/v0.1.23.3/task2/topology_alignment"
BASE_PROTOCOL = base.protocol
BASE_SPECS = previous.BASE_SPECS
BASE_CODE = previous.BASE_CODE


def new_out(path):
    path = Path(path).resolve()
    if RESULT_ROOT not in path.parents or not path.name.startswith("probe_"):
        raise ValueError("use a new topology_alignment/probe_* directory")
    path.mkdir(parents=True, exist_ok=False)
    path.chmod(0o777)
    return path


def frozen_code():
    result = BASE_CODE()
    for path in [*HERE.glob("*.py"), *DIAGNOSIS.glob("*.py")]:
        result[str(path.relative_to(inventory.ROOT))] = inventory.digest(path)
    return result


def inspect(state, session, hca="shca_0", ifname="ib0", port=None, protect_sessions=()):
    value = previous.BASE_INSPECT(state, session, hca, ifname, port, protect_sessions)
    accessible = sum(row["accessible"] for row in value["protected_legacy_endpoints"])
    if accessible < 14:
        value["resource_admission"]["errors"].append("fewer than 14 protected legacy keepers accessible")
    errors = value["resource_admission"]["errors"]
    value["resource_admission"].update(passed=not errors, status="blocked" if errors else "passed")
    return value


def process_specs(state, workers, out, args):
    rows = BASE_SPECS(state, workers, out, args)
    for row in rows:
        path = Path(row["spec"])
        value = json.loads(path.read_text())
        command = value["command"]
        command[1] = str(HERE / "rank_entry.py")
        hca = args.hca if row["rank"] % 2 == 0 else args.flow_b_hca
        command[command.index("--hca") + 1] = hca
        value["env"].update(NCCL_IB_HCA=hca,
            NCCL_TOPO_DUMP_FILE=str(out / f"rank_{row['rank']}.topology.xml"),
            NCCL_TOPO_DUMP_FILE_RANK=str(0 if row["rank"] < 2 else 1))
        # The process launcher merges os.environ; only an exec-level unset is reliable.
        if args.topology_mode == "unset":
            value["env"].pop("NCCL_TOPO_FILE", None)
            value["command"] = ["env", "-u", "NCCL_TOPO_FILE", *command]
        else:
            value["env"]["NCCL_TOPO_FILE"] = str(args.xml)
        value["topology_control"] = {"mode": args.topology_mode,
            "dump_rank_policy": "requested own pair-local rank; support requires startup output confirmation",
            "flow_hca": hca, "stage": args.stage, "pre_import_environment": True}
        inventory.write(path, value)
        row["spec_sha256"] = inventory.digest(path)
    return rows


def deferred_collect(out, prereg, hca, ifname):
    deadline = time.monotonic() + 90
    while not all((out / f"rank_{rank}.libraries.json").exists() for rank in range(4)):
        if time.monotonic() >= deadline:
            raise TimeoutError("post-measurement library fingerprint deadline expired")
        time.sleep(.2)
    observations, _, ranks = previous.BASE_COLLECT(out, prereg, hca, ifname)
    # No pre-exit log visibility can certify transport. Preserve this raw stage as pending.
    return observations, [], ranks


def topology_shape(raw):
    root = ET.fromstring(raw)
    return {"root_tag": root.tag, "gpu_count": len(root.findall(".//gpu")),
        "nic_count": len(root.findall(".//nic")),
        "cpu_nodes": [dict(item.attrib) for item in root.findall(".//cpu")],
        "gpu_attributes": [dict(item.attrib) for item in root.findall(".//gpu")],
        "net_attributes": [dict(item.attrib) for item in root.findall(".//net")],
        "xml_parse_passed": True}


def postexit(result, args):
    source = Path(result["out"])
    raw_summary = source / "summary.json"
    original_hash = inventory.digest(raw_summary)
    destination = source / "postexit"
    destination.mkdir(exist_ok=False)
    state = inventory.load_session(result["session"])
    artifacts, bindings, environments, graphs = [], [], [], []
    checks = {"owned_cleanup_passed": result["cleanup"]["passed"],
        "protected_state_preserved": result.get("before_after_preservation", {}).get("passed", False),
        "code_preserved": result["code_preservation"]["passed"],
        "inputs_preserved": result["input_preservation"]["passed"]}
    for entry in sorted(result["processes"], key=lambda e: e["rank"]):
        rank, expected = entry["rank"], entry.get("start_reply")
        if not expected:
            raise ValueError("post-exit evidence lacks an owned launch identity")
        spec_path = Path(entry["spec"])
        registered = json.loads(spec_path.with_suffix(".pid.json").read_text())
        spec = json.loads(spec_path.read_text())
        names = [f"rank_{rank}.environment.json", f"rank_{rank}.libraries.json",
            f"rank_{rank}.topology.xml",
            Path(spec["env"]["NCCL_DEBUG_FILE"].replace("%p", str(expected["pid"]))).name]
        for name in names:
            path = source / name
            fetched = json.loads(inventory.remote(state, entry["node"],
                ["python3", str(HERE / "read_evidence.py"), "--session", result["session"],
                 "--artifact", str(path), "--pid", str(expected["pid"])], timeout=90))
            validate_process_exit(expected, registered, fetched["process"])
            fetched.update(rank=rank, node=entry["node"])
            if fetched["exists"]:
                raw = base64.b64decode(fetched.pop("content_base64"), validate=True)
                remote_hash = hashlib.sha256(raw).hexdigest()
                local_hash = inventory.digest(path) if path.exists() else None
                if remote_hash != fetched["sha256"] or remote_hash != local_hash:
                    raise ValueError(f"remote/local post-exit hash differs: {path}")
                saved = destination / name
                saved.write_bytes(raw)
                fetched.update(saved_path=str(saved), local_sha256=local_hash,
                               saved_sha256=inventory.digest(saved))
                if name.endswith("environment.json"):
                    env = json.loads(raw)
                    environments.append(env)
                    wanted = str(args.xml) if args.topology_mode == "xml" else None
                    checks[f"rank_{rank}_preimport_environment"] = (env["pre_torch_import"]
                        and not env["torch_imported"] and env["pid"] == expected["pid"]
                        and env["effective_environment"].get("NCCL_TOPO_FILE") == wanted
                        and env["effective_environment"]["NCCL_TOPO_DUMP_FILE"] == spec["env"]["NCCL_TOPO_DUMP_FILE"])
                elif name.endswith("topology.xml"):
                    graphs.append(dict(topology_shape(raw), rank=rank, sha256=remote_hash,
                                       path=str(saved)))
                elif name.endswith(".log"):
                    text = raw.decode(errors="replace")
                    checks[f"rank_{rank}_owned_pid_in_log"] = any(
                        f":{expected['pid']}:" in line and "NET/IB : Using" in line for line in text.splitlines())
            elif not name.endswith("topology.xml"):
                checks[f"rank_{rank}_required_{name}"] = False
            artifacts.append(fetched)
        bindings.append(previous.binding(destination, rank, spec["env"]["NCCL_IB_HCA"], args.ifname))
    prereg = json.loads((source / "protocol.json").read_text())
    observations, _, _ = previous.BASE_COLLECT(source, prereg, args.hca, args.ifname)
    for observation in observations:
        observation["actual_nic_payload_evidence"] = previous.per_port_payload(observation["flows"], bindings)
    checks.update(all_rank_bindings_passed=all(b["passed"] for b in bindings),
        all_actual_port_payloads_passed=all(o["actual_nic_payload_evidence"]["passed"] for o in observations),
        all_background_estimates_below_5pct=(args.stage == "startup" or all(
            o["actual_nic_payload_evidence"]["background_fraction_estimate"] <= .05 for o in observations)),
        all_rank_environment_records=len(environments) == 4,
        actual_graph_dump_available=bool(graphs),
        all_four_rank_dumps_available=len(graphs) == 4,
        original_summary_unchanged=inventory.digest(raw_summary) == original_hash,
        all_rank_content_and_timing=len(observations) == len(prereg["cases"]))
    checks["all_library_fingerprints_available"] = all(
        item["exists"] for item in artifacts if item["path"].endswith("libraries.json"))
    graph_ranks = {g["rank"] for g in graphs}
    derived = dict(result)
    derived.pop("error", None)
    derived.update(status="passed" if all(checks.values()) else "failed", out=str(destination),
        raw_stage_status=result["status"], source_summary_sha256=original_hash,
        observations=observations, binding=bindings, environment=environments,
        runtime_topology={"graphs": graphs, "dump_missing_ranks": sorted(set(range(4))-graph_ranks),
            "coverage": "all_ranks" if len(graph_ranks) == 4 else "partial",
            "discovery_classification": "not_inferred_from_unset; inspect vendor input/fallback log lines and dump",
            "receiver_graph_certified": {str(rank): rank in graph_ranks for rank in (2, 3)}},
        postexit={"checks": checks, "artifacts": artifacts, "new_gpu_observations": 0},
        topology_control={"mode": args.topology_mode, "stage": args.stage,
            "xml_requested": str(args.xml) if args.topology_mode == "xml" else None,
            "flow_a_hca": args.hca, "flow_b_hca": args.flow_b_hca,
            "measurement_scope": "startup mechanism only" if args.stage == "startup" else "matched A/B/pair network microprobe"},
        shared_resource=shared_gate(observations, all(b["passed"] for b in bindings),
            checks["protected_state_preserved"], checks["owned_cleanup_passed"]))
    inventory.write(destination / "summary.json", derived)
    inventory.write(destination / "manifest.json", {"source_summary_sha256": original_hash,
        "raw_summary_unchanged": inventory.digest(raw_summary) == original_hash,
        "new_gpu_observations": 0, "files": {str(p): inventory.digest(p)
             for p in sorted(destination.iterdir()) if p.is_file()}})
    return derived


def configure(args):
    def selected_protocol(workers, session_sha256, matrix, reverse):
        value = BASE_PROTOCOL(workers, session_sha256, "network", reverse)
        if args.stage == "startup":
            value["cases"] = [value["cases"][-1]]
            case = value["cases"][0]
            case.update(buffer_bytes=1 << 20, iterations=1, bytes_per_flow=1 << 20)
            value["resource_limits"]["synthetic_hbm_bytes_per_gpu"] = 1 << 20
            value["samples_per_configuration"] = 0
            value["startup_only"] = True
        return value
    base.new_out = new_out
    base.process_specs = process_specs
    base.frozen_code = frozen_code
    base.inspect = inspect
    base.collect = deferred_collect
    base.protocol = selected_protocol
    base.SCRIPT = HERE / "read_evidence.py"
    inventory.SCRIPT = base.SCRIPT


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--topology-mode", choices=("xml", "unset"), required=True)
    parser.add_argument("--xml", type=Path, default=Path("/usr/local/built-in-508-topo-input-tj-default.xml"))
    parser.add_argument("--stage", choices=("startup", "measurement"), default="measurement")
    parser.add_argument("--flow-b-hca", choices=("shca_0", "shca_1"), default="shca_0")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--protect-session", type=Path, action="append", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or not 1 <= args.timeout <= 900:
        parser.error("port or deadline outside bounded protocol")
    args.hca, args.ifname, args.matrix, args.reverse = "shca_0", "ib0", "network", False
    configure(args)
    result = base.driver(args)
    if args.execute and result.get("observations"):
        try:
            result = postexit(result, args)
        except BaseException as exc:
            inventory.write(Path(result["out"]) / "postexit_error.json", {"status": "failed", "error": repr(exc),
                "raw_summary_sha256": inventory.digest(Path(result["out"]) / "summary.json"),
                "new_gpu_observations": 0})
            print(json.dumps({"status": "failed", "error": repr(exc), "out": result["out"]}))
            raise SystemExit(1)
    print(json.dumps({"status": result["status"], "out": result["out"]}))
    if result["status"] not in ("passed", "registered_only"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
