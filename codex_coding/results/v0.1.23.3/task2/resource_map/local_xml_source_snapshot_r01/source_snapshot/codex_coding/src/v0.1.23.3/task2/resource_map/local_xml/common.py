"""CPU-only contracts for one local, bounded XML functional observation."""
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
RESULT_ROOT = ROOT / "codex_coding/results/v0.1.23.3/task2/resource_map"
sys.path.insert(0, str(ROOT / "main_lib"))
sys.path.insert(0, str(ROOT / "codex_coding/src/v0.1.23.3/task2/topology_scenarios/calibration"))
import inventory
from fluidckpt.topology.rccl_xml import validate_physical_xml

NODE = "node27"
CONTAINER = "zjn-fluid-basic-1bce3897a2075b"
PAYLOAD = 1 << 20
GUARD = 2 << 30
FREE_TOLERANCE = 128 << 20
ORIGINAL_FILES = ("/usr/local/built-in-508-topo-input-tj-default.xml", "/opt/dtk/rccl/lib/topo_mapping_default.xml")
PROCESS = ROOT / "codex_coding/src/task2/cluster/process.py"
EXTRA_ENV = ("CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "LD_LIBRARY_PATH", "PYTHONPATH",
             "GLOO_SOCKET_IFNAME", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "HSA_ENABLE_SDMA",
             "HSA_FORCE_FINE_GRAIN_PCIE", "AMD_SERIALIZE_KERNEL")
digest, write = inventory.digest, inventory.write


def owned(path):
    path = Path(path).resolve()
    if path.parent != RESULT_ROOT or not path.name.startswith("local_xml_"):
        raise ValueError("require a new resource_map/local_xml_* directory")
    return path


def local_state(session):
    state = inventory.load_session(session)
    if state["nodes"][NODE]["container"] != CONTAINER:
        raise ValueError("unexpected local container")
    if state["members"][:2] != [[NODE, 0], [NODE, 1]]:
        raise ValueError("local workers must be retained node27 GPUs 0/1")
    if state["endpoints"][:2] != ["http://12.11.3.27:33000", "http://12.11.3.27:33001"]:
        raise ValueError("unexpected protected local keeper endpoints")
    return state


def input_xml(directory):
    directory = Path(directory).resolve()
    if directory.parent != RESULT_ROOT or not directory.name.startswith("xml_"):
        raise ValueError("require a versioned owned XML directory")
    manifest_path = directory / "node27.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    path = Path(manifest["input_file"]).resolve()
    if path != directory / "node27.physical.gdr0.xml" or manifest["node"] != NODE:
        raise ValueError("local XML identity mismatch")
    if digest(path) != manifest["sha256"]:
        raise ValueError("local input SHA mismatch")
    sources = {}
    for ref in manifest["source_refs"]:
        source = Path(ref["path"])
        if digest(source) != ref["sha256"]:
            raise ValueError("physical source SHA mismatch")
        sources[source.stem] = json.loads(source.read_text())
    if not validate_physical_xml(path.read_bytes(), sources["host"], sources["container"])["passed"]:
        raise ValueError("local physical XML validation failed")
    return path, manifest_path, manifest


def catalog_hash(workers):
    # Full replicas include metadata, index, epochs and leases. Physical free is separate.
    fields = ("worker", "node", "gpu", "numa", "pid", "epoch", "session", "replicas", "transactions",
              "transaction_phases", "used_bytes", "hbm_allocated_bytes", "hbm_reserved_bytes", "staging_bytes")
    rows = [{k: w[k] for k in fields} for w in workers]
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def gpu_paths(raw):
    root = ET.fromstring(raw)
    paths = {}
    def visit(element, cpu=None, chain=()):
        if element.tag == "cpu":
            cpu = dict(element.attrib)
        if element.tag == "pci":
            chain = (*chain, element.attrib["busid"].lower())
        if element.tag == "gpu":
            if not chain or cpu is None or chain[-1] in paths:
                raise ValueError("missing or duplicate GPU physical ancestry")
            paths[chain[-1]] = {"cpu": cpu, "chain": list(chain), "gpu": dict(element.attrib)}
        for child in element:
            visit(child, cpu, chain)
    visit(root)
    return paths


def mapping_check(input_raw, dump_raw, bdf, rank):
    source, actual = gpu_paths(input_raw), gpu_paths(dump_raw)
    want, got = source.get(bdf), actual.get(bdf)
    checks = {"selected_bdf_present": bool(want and got),
              "full_pci_chain_matches": bool(want and got and want["chain"] == got["chain"]),
              "numa_matches": bool(want and got and want["cpu"]["numaid"] == got["cpu"].get("numaid")),
              "affinity_matches": bool(want and got and want["cpu"]["affinity"] == got["cpu"].get("affinity")),
              "rank_matches": bool(got and got["gpu"].get("rank") == str(rank)),
              "dev_matches": bool(got and got["gpu"].get("dev") == str(rank)),
              "gdr0_policy_matches": bool(got and got["gpu"].get("gdr") == "0")}
    return {"passed": all(checks.values()), "checks": checks, "bdf": bdf, "input": want, "actual": got,
            "scope": "selected local rank/GPU ancestry and policy; no NIC/GDR or whole-machine certification"}


def free_check(before, after):
    rows = []
    for old, new in zip(before, after):
        a, b = old["headroom_bytes"]["hbm"], new["headroom_bytes"]["hbm"]
        rows.append({"gpu": old["gpu"], "before_bytes": a, "after_bytes": b, "delta_bytes": b-a,
                     "unrecovered_bytes": max(0, a-b),
                     "passed": (old["gpu"] == new["gpu"] and max(0, a-b) <= FREE_TOLERANCE)})
    return {"passed": len(rows) == 2 and all(r["passed"] for r in rows),
            "tolerance_bytes": FREE_TOLERANCE, "rows": rows}
