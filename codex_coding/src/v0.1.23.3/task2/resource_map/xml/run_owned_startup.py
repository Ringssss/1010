"""Per-node physical XML adapter around the frozen, bounded startup probe."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import resource
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
sys.path.insert(0, str(ROOT / "main_lib"))
sys.path.insert(0, str(ROOT / "codex_coding/src/v0.1.23.3/task2/topology_alignment/probe"))
import run_probe as probe
from fluidckpt.topology.rccl_xml import validate_physical_xml
from fluidckpt.benchmarks.topology_calibration import shared_gate, validate_process_exit
base, previous, inventory = probe.base, probe.previous, probe.inventory
topology_shape = probe.topology_shape


def load_node_xml(directory):
    directory = directory.resolve()
    allowed = ROOT / "codex_coding/results/v0.1.23.3/task2/resource_map"
    if directory.parent != allowed or not directory.name.startswith("xml_"):
        raise ValueError("require owned versioned XML directory")
    result = {}
    for node in ("node27", "node29"):
        manifest = json.loads((directory / (node + ".manifest.json")).read_text())
        path = Path(manifest["input_file"]).resolve()
        if path.parent != directory or manifest["node"] != node or inventory.digest(path) != manifest["sha256"]:
            raise ValueError("node XML path/identity/SHA mismatch")
        sources = {}
        for source in manifest["source_refs"]:
            source_path = Path(source["path"])
            if inventory.digest(source_path) != source["sha256"]:
                raise ValueError("physical source SHA mismatch")
            sources[source_path.stem] = json.loads(source_path.read_text())
        if not validate_physical_xml(path.read_bytes(), sources["host"], sources["container"])["passed"]:
            raise ValueError("physical XML validation failed")
        result[node] = path
    return result


def process_specs(state, workers, out, args):
    rows = probe.process_specs(state, workers, out, args)
    for row in rows:
        value = json.loads(Path(row["spec"]).read_text())
        input_path = args.node_xml[row["node"]]
        dump_path = Path(value["env"]["NCCL_TOPO_DUMP_FILE"]).resolve()
        if dump_path == input_path or dump_path.parent != Path(out).resolve():
            raise ValueError("dump must be a distinct new run artifact")
        value["env"]["NCCL_TOPO_FILE"] = str(input_path)
        value["topology_control"].update(node=row["node"], input_sha256=inventory.digest(input_path), runtime_accepted=False)
        inventory.write(Path(row["spec"]), value)
        row["spec_sha256"] = inventory.digest(Path(row["spec"]))
    return rows


# This evidence function is copied from the frozen probe; only per-node expected
# XML lookup and the requested-XML summary field differ. No GPU kernel is added.
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
                ["python3", str(HERE / "read_owned_evidence.py"), "--session", result["session"],
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
                    wanted = str(args.node_xml[entry["node"]])
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
            "xml_requested_by_node": {node: str(path) for node, path in args.node_xml.items()},
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


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml-directory", type=Path, required=True)
    parser.add_argument("--session", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--port", type=int)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--protect-session", type=Path, action="append", default=[])
    parser.add_argument("--emit-plan", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    args.node_xml = load_node_xml(args.xml_directory)
    if args.emit_plan:
        if args.execute:
            parser.error("emit-plan cannot execute")
        print(json.dumps({"node_xml": {n: str(p) for n,p in args.node_xml.items()},
            "gpu_operations": 0, "planned_gpu_ordinals_per_node": [0,1], "planned_processes": 4,
            "planned_payload_bytes_per_rank": 1 << 20, "performance_measurement": False,
            "runtime_accepted": False}, indent=2))
        return
    if not args.session or not args.out or not args.port or not args.protect_session:
        parser.error("startup requires session/out/port/protect-session")
    if not 1024 <= args.port <= 65535 or not 1 <= args.timeout <= 300:
        parser.error("port/deadline outside bounded startup")
    args.xml = args.node_xml["node27"]
    args.topology_mode, args.stage = "xml", "startup"
    args.hca, args.flow_b_hca, args.ifname, args.matrix, args.reverse = "shca_0", "shca_0", "ib0", "network", False
    probe.configure(args)
    def new_out(path):
        path = Path(path).resolve()
        allowed = ROOT / "codex_coding/results/v0.1.23.3/task2/resource_map"
        if path.parent != allowed or not path.name.startswith("xml_startup_"):
            raise ValueError("require new resource_map/xml_startup_* directory")
        path.mkdir(parents=True, exist_ok=False)
        path.chmod(0o777)
        return path
    def frozen_code():
        hashes = probe.frozen_code()
        for path in [Path(__file__).resolve(), HERE / "read_owned_evidence.py", ROOT / "main_lib/fluidckpt/topology/rccl_xml.py", *args.node_xml.values(),
                     *(args.xml_directory.resolve() / (node + ".manifest.json") for node in args.node_xml)]:
            hashes[str(path.relative_to(ROOT))] = inventory.digest(path)
        return hashes
    base.new_out, base.process_specs, base.frozen_code = new_out, process_specs, frozen_code
    base.SCRIPT = HERE / "read_owned_evidence.py"
    inventory.SCRIPT = base.SCRIPT
    result = base.driver(args)
    if args.execute and result.get("observations"):
        try:
            result = postexit(result, args)
        except BaseException as exc:
            inventory.write(Path(result["out"]) / "postexit_error.json", {"status": "failed", "error": repr(exc), "new_gpu_observations": 0})
            raise
    print(json.dumps({"status": result["status"], "out": result["out"], "performance_measurement": False}))
    if result["status"] not in ("passed", "registered_only"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
