"""Build a new, evidence-bound physical subgraph and CPU-only layout contracts."""
import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.benchmarks.calibrated_recovery import comparison_protocol
from fluidckpt.benchmarks.topology_cases import canonical_hash
from fluidckpt.cluster.path_catalog import topology_id
from fluidckpt.executor.chunks import STAGING_BYTES
from fluidckpt.topology.rccl_xml import physical_xml
from fluidckpt.topology.resource_map import evidence_ref, planner_readiness, validate_resource_map

RESULTS = ROOT / "codex_coding/results/v0.1.23.3/task2"
QUALIFICATION = RESULTS / "three_node_recovery/review_runtime_final_r01/qualification.json"
XML_MANIFEST = RESULTS / "three_node_recovery/probe_xml_2830_gdr0_r01/manifest.json"


def load(path):
    return json.loads(Path(path).read_text())


def physical_subgraph():
    qualification, xml = load(QUALIFICATION), load(XML_MANIFEST)
    refs = [evidence_ref(QUALIFICATION), evidence_ref(XML_MANIFEST)]
    registry, nodes = {}, {}
    selected = {"node28": {"0000:09:00.0", "0000:55:00.0"},
                "node30": {"0000:09:00.0", "0000:85:00.0"}}
    for manifest in xml["nodes"]:
        node = manifest["node"]
        source = Path(manifest["source_refs"][0]["path"])
        if evidence_ref(source) != manifest["source_refs"][0]:
            raise ValueError("inventory source SHA changed")
        host = load(source)
        raw, _ = physical_xml(host)
        if raw != Path(manifest["input_file"]).read_bytes():
            raise ValueError("existing physical XML builder no longer reproduces frozen input")
        refs.extend([evidence_ref(source), evidence_ref(manifest["input_file"])])
        if host["boot_id"] != qualification["nodes"][node]["boot_id"]:
            raise ValueError("physical and functional evidence boot mismatch")
        nodes[node] = dict(id=node, hostname=host["hostname"], boot_id=host["boot_id"],
                           physical_sampled_at=host["started_utc"], source_refs=[evidence_ref(source)])
        gpu_ordinals = {row["pci_chain"][0]["bdf"]: i for i, row in enumerate(
            sorted(host["kfd"], key=lambda row: row["pci_chain"][0]["bdf"]))}
        for kind, devices in (("gpu", host["kfd"]), ("hca", host["hca"])):
            for device in devices:
                chain = device["pci_chain"]
                if kind == "gpu" and chain[0]["bdf"] not in selected[node]:
                    continue
                if kind == "hca" and device["name"] != "shca_0":
                    continue
                numa_id = f"{node}/numa/{chain[0]['numa_node']}"
                registry.setdefault(numa_id, dict(id=numa_id, node=node, kind="numa",
                    numa=int(chain[0]["numa_node"]), source_refs=[evidence_ref(source)],
                    cpulist=host["numa"]["node" + chain[0]["numa_node"]]["cpulist"]))
                ids = [f"{node}/pci/{entry['bdf']}" for entry in chain]
                for index, (rid, entry) in enumerate(zip(ids, chain)):
                    row = dict(id=rid, node=node, kind=kind if index == 0 else "pci_bridge",
                               bdf=entry["bdf"], numa_id=numa_id,
                               pci_chain_leaf_to_root=ids[index:], source_refs=[evidence_ref(source)])
                    if index == 0 and kind == "gpu":
                        row.update(logical_gpu=gpu_ordinals[entry["bdf"]],
                                   ordinal_basis="physical sorted-BDF inventory; HIP runtime mapping remains separate")
                    if rid in registry and registry[rid]["kind"] != row["kind"]:
                        raise ValueError("conflicting PCI identity")
                    registry.setdefault(rid, row)
                if kind == "hca":
                    for port in device["ports"]:
                        rid = f"{node}/hca/{device['name']}/port/{port}"
                        registry[rid] = dict(id=rid, node=node, kind="hca_port", device_id=ids[0],
                                             name=device["name"], port=int(port), numa_id=numa_id,
                                             pci_chain_leaf_to_root=ids, source_refs=[evidence_ref(source)])
    def chain(node, bdf):
        return registry[f"{node}/pci/{bdf}"]["pci_chain_leaf_to_root"]
    def path(src_bdf, dst_bdf, certified):
        ids = (chain("node28", src_bdf) + chain("node30", dst_bdf)
               + chain("node28", "0000:03:00.0") + chain("node30", "0000:03:00.0")
               + [f"{node}/hca/shca_0/port/1" for node in nodes]
               + [f"{node}/numa/0" for node in nodes])
        return dict(src=f"node28/pci/{src_bdf}", dst=f"node30/pci/{dst_bdf}",
                    src_tier="HBM", dst_tier="HBM", resource_ids=sorted(set(ids)),
                    runtime_config_ids=["qualified_r03_gpu2_gpu0"] if certified else [],
                    runtime_scope="observed selected GPU/NIC transport" if certified else "physical candidate only",
                    source_refs=copy.deepcopy(refs))
    paths = {"qualified_28gpu2_30gpu0": path("0000:55:00.0", "0000:09:00.0", True),
             "candidate_28gpu0_30gpu4": path("0000:09:00.0", "0000:85:00.0", False)}
    mapping = dict(schema_version=1, development="v0.1.23.3/task2", source_refs=refs,
        physical=dict(nodes=nodes, resources=registry, directed_paths=paths,
                      fabric=dict(internal_topology=None, capacity_Bps=None, sharing_interference=None)),
        runtime_configs={"qualified_r03_gpu2_gpu0": dict(fingerprint=refs[0]["sha256"],
            runtime_observed=True, observed_resource_ids=paths["qualified_28gpu2_30gpu0"]["resource_ids"],
            qualification_ref=refs[0], actual_transport="IBext_v8", HCA="shca_0:1", gdr=0),
            "candidate_gpu0_gpu4": dict(fingerprint=None, runtime_observed=False, observed_resource_ids=[])},
        availability=dict(context_id=None, resources={}), measurements=[], shared_resource_contracts={},
        planning_readiness=dict(performance_ready=False,
            reason="no current authorized resource/owner snapshot or calibrated workpoint"))
    candidate_refs = []
    for node, bdf in (("node28", "0000:09:00.0"), ("node30", "0000:85:00.0")):
        file = RESULTS / f"three_node_recovery/probe_minimal_candidate_2830_{node}_r01/summary.json"
        if not file.exists():
            break
        observed = load(file)
        child = observed.get("child_result", {})
        if (observed.get("passed") is not True or observed.get("xml_runtime_accepted") is not True
                or child.get("actual_bdf") != bdf or child.get("selected_gpu_mapping", {}).get("passed") is not True
                or child.get("selected_net_mapping", {}).get("passed") is not True):
            raise ValueError("new candidate qualification mismatched")
        candidate_refs.append(evidence_ref(file))
    if len(candidate_refs) == 2:
        paths["candidate_28gpu0_30gpu4"].update(runtime_config_ids=["candidate_gpu0_gpu4"],
            runtime_scope="qualified two-rank 1MiB functionality; new worker communicator still needs its own review",
            source_refs=paths["candidate_28gpu0_30gpu4"]["source_refs"] + candidate_refs)
        mapping["source_refs"].extend(candidate_refs)
        mapping["runtime_configs"]["candidate_gpu0_gpu4"].update(fingerprint=canonical_hash(candidate_refs),
            runtime_observed=True, observed_resource_ids=paths["candidate_28gpu0_30gpu4"]["resource_ids"],
            source_refs=candidate_refs, application_executor_qualified=False)
    check = validate_resource_map(mapping)
    if not check["passed"]:
        raise ValueError(check)
    return mapping


def block_case(nbytes, chunk_bytes):
    workers = [dict(worker=i, node=node, gpu=gpu, numa=numa, epoch="CPU-unbound",
                    capabilities=["local_dram", "local_hbm", "peer", "network"])
               for i, (node, gpu, numa) in enumerate((("node28", 0, 0), ("node30", 0, 4)))]
    base = [dict(id=f"CPU-base/{tp}", identity=f"synthetic-blocks/rank{tp}", tp=tp, worker=0,
                 node="node28", gpu=0, numa=0, tier="dram", off=0, n=nbytes,
                 state="VALID", leases=0, allocated_bytes=nbytes, epoch="CPU-unbound", generation=1)
            for tp in (0, 1)]
    candidates = [dict(base[tp], id=f"CPU-cache/{tp}/{worker}", worker=worker, tier="hbm",
                       node=workers[worker]["node"], gpu=workers[worker]["gpu"], numa=workers[worker]["numa"],
                       preview_only=True) for tp in (0, 1) for worker in (0, 1)]
    targets = [dict(key=f"CPU-target/{tp}", identity=base[tp]["identity"], tp=tp, worker=tp,
                    tier="hbm", off=0, n=nbytes, mode="replica") for tp in (0, 1)]
    layouts = {"source_concentrated": ["CPU-cache/0/0", "CPU-cache/1/0"],
               "target_local_distributed": ["CPU-cache/0/0", "CPU-cache/1/1"]}
    case = dict(schema_version="topology_case.v1", development="v0.1.23.3/task2",
        case_id=f"synthetic_blocks_{nbytes}", workers=workers, topology_id=topology_id(workers),
        collection_kind="synthetic_blocks", physical_binding_by_worker={
            "0": dict(logical_gpu=0, physical_gpu=0, resource_id="node28/pci/0000:09:00.0", numa=0),
            "1": dict(logical_gpu=0, physical_gpu=4, resource_id="node30/pci/0000:85:00.0", numa=4)},
        scenario_semantics="mutually_exclusive_restores", objective="worst",
        format_contract=dict(model_version="synthetic_only", dtype="uint8", quantization="none", tp=2,
                             processed_snapshot_id=[row["identity"] for row in base]),
        base=base, candidates=candidates, restore_scenarios=[dict(name="both_targets", weight=1, targets=targets)],
        fixed_layouts=[dict(name=name, candidate_ids=ids) for name, ids in layouts.items()],
        budget=dict(tier_budgets=dict(hbm=2*nbytes), location_budgets={f"{i}:hbm": 2*nbytes for i in (0, 1)},
                    base_dram_bytes=2*nbytes, target_allocation_bytes=2*nbytes, staging_bytes=2*STAGING_BYTES,
                    staging_by_location={f"{i}:hbm": STAGING_BYTES for i in (0, 1)},
                    budget_origin="externally_assigned_scenario_constraints"),
        execution_contract=dict(schedule="rank_interleaved", verification_position="before_replica_publish"),
        demand_origin="synthetic_preregistered_weights_not_trace_prediction")
    profile = dict(rates=dict(local_dram=1, local_hbm=1, peer=1, network=1),
                   gpu_ingress_GBps=1, gpu_egress_GBps=1, dram_GBps=1, network_node_GBps=1,
                   serial_target_execution=True, provenance="CPU functional placeholders; no measured rate")
    return case, comparison_protocol(case, layouts, profile, chunk_bytes=chunk_bytes,
                                     load_state="preregistered; real warm/cold state pending session binding")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.parent != RESULTS / "minimal_calibration" or not out.name.startswith("integration_"):
        parser.error("require a new minimal_calibration/integration_* result directory")
    out.mkdir(parents=True, exist_ok=False)
    mapping = physical_subgraph()
    (out / "resource_map.json").write_text(json.dumps(mapping, indent=2) + "\n")
    for mib in (16, 64, 256):
        case, protocol = block_case(mib << 20, 32 << 20)
        protocol.update(execution_authorized=mib == 16,
                        execution_scope="two 16MiB blocks after mesh budget gate only" if mib == 16 else "CPU candidate; not authorized for GPU execution")
        protocol.pop("protocol_sha256")
        protocol["protocol_sha256"] = canonical_hash(protocol)
        (out / f"case_{mib}MiB.json").write_text(json.dumps(case, indent=2) + "\n")
        (out / f"protocol_{mib}MiB.json").write_text(json.dumps(protocol, indent=2) + "\n")
    report = dict(structure=validate_resource_map(mapping), readiness=planner_readiness(mapping),
                  new_GPU_samples=0, remote_operations=0, old_resource_map_written=False)
    (out / "CPU_contract_summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(dict(out=str(out), **report)))


if __name__ == "__main__":
    main()
