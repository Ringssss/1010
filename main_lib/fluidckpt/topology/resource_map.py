"""Evidence-backed resource lookup and fail-closed planning admission (no solver)."""
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

SCHEMA_VERSION = 1
PATH_FIELDS = ("src", "dst", "src_tier", "dst_tier")


class PlanningNotReady(ValueError):
    def __init__(self, readiness):
        self.readiness = readiness
        super().__init__("planning input rejected: " + "; ".join(readiness["reasons"]))


def evidence_ref(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def load_resource_map(path, verify_sources=False):
    mapping = json.loads(Path(path).read_text())
    check = validate_resource_map(mapping)
    if not check["passed"]:
        raise ValueError("invalid resource map: " + "; ".join(check["errors"]))
    if verify_sources:
        for ref in mapping.get("source_refs", []):
            if evidence_ref(ref["path"])["sha256"] != ref["sha256"]:
                raise ValueError("source changed: " + ref["path"])
    return mapping


def resources(mapping, kind=None, node=None):
    return [copy.deepcopy(row) for row in mapping["physical"]["resources"].values()
            if (kind is None or row["kind"] == kind) and (node is None or row.get("node") == node)]


def resource(mapping, resource_id):
    return copy.deepcopy(mapping["physical"]["resources"][resource_id])


def shared_ancestors(mapping, *resource_ids):
    """Return unique PCI ancestor identities; topology grouping implies no rate cap."""
    if not resource_ids:
        return []
    rows = [resource(mapping, rid) for rid in resource_ids]
    chains = [set(r.get("pci_chain_leaf_to_root", [r["id"]])) for r in rows]
    common = set.intersection(*chains)
    return [resource(mapping, rid) for rid in sorted(common)
            if mapping["physical"]["resources"][rid]["kind"] == "pci_bridge"]


def path_evidence(mapping, path=None, primitive=None, runtime_config_id=None):
    return [copy.deepcopy(row) for row in mapping["measurements"]
            if (primitive is None or row.get("primitive") == primitive)
            and (runtime_config_id is None or row.get("runtime_config_id") == runtime_config_id)
            and (path is None or any(all(p.get(k) == path.get(k) for k in PATH_FIELDS)
                                     for p in row.get("directed_paths", [])))]


def _timestamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _admitted(mapping, request, path, now, max_age_s):
    if "workpoint" in request:
        return finite_workpoint_evidence(mapping, request, now=now, max_age_s=max_age_s, require_profile=True)
    admitted = []
    fingerprint = mapping.get("runtime_configs", {}).get(request.get("runtime_config_id"), {}).get("fingerprint")
    for row in path_evidence(mapping, path, request.get("primitive"), request.get("runtime_config_id")):
        rate = row.get("rate_Bps")
        try:
            age = now - _timestamp(row["measured_at"])
        except (KeyError, ValueError, TypeError):
            continue
        if (row.get("admission", {}).get("current_profile_eligible") is True
                and fingerprint and row.get("runtime_config_fingerprint") == fingerprint
                and len(row.get("directed_paths", [])) == 1
                and row.get("status") == "verified_current" and row.get("source_refs")
                and row.get("timing", {}).get("planning_compatible") is True
                and 0 <= age <= max_age_s and isinstance(rate, (int, float))
                and not isinstance(rate, bool) and math.isfinite(rate) and rate > 0
                and ("workpoint" not in request or row.get("workpoint") == request["workpoint"])
                and row.get("availability_context_id") == mapping["availability"].get("context_id")):
            admitted.append(row)
    return admitted


def _path_identity(path):
    return tuple(path.get(field) for field in PATH_FIELDS)


def _workpoint_valid(workpoint, paths, resource_ids, primitive):
    """A finite workpoint is an exact workload, never a reusable hardware cap."""
    if not isinstance(workpoint, dict):
        return False
    sizes = workpoint.get("path_bytes", [])
    incidence = workpoint.get("resource_incidence", [])
    budgets = workpoint.get("bytes_budget_by_resource", {})
    return (bool(workpoint.get("workload_id")) and workpoint.get("operation") == primitive
            and type(workpoint.get("concurrency")) is int
            and 1 <= workpoint["concurrency"] <= len(paths)
            and bool(workpoint.get("schedule"))
            and set(budgets) == set(resource_ids)
            and all(type(n) is int and n >= 0 for n in budgets.values())
            and len(sizes) == len(paths) and all(type(n) is int and n > 0 for n in sizes)
            and len(incidence) == len(paths)
            and all(isinstance(ids, list) and ids and len(ids) == len(set(ids))
                    and set(ids) <= set(resource_ids)
                    and path.get("src") in ids and path.get("dst") in ids
                    for ids, path in zip(incidence, paths)))


def _admitted_joint(mapping, request, now, max_age_s):
    return finite_workpoint_evidence(mapping, request, now=now, max_age_s=max_age_s, require_profile=True)


def finite_workpoint_evidence(mapping, request, *, now=None, max_age_s=300, require_profile=True):
    """Select exact finite observations; observational evidence grants no admission."""
    now = datetime.now(timezone.utc).timestamp() if now is None else _timestamp(now)
    paths, workpoint = request.get("directed_paths", []), request.get("workpoint")
    if not _workpoint_valid(workpoint, paths, request.get("resource_ids", []), request.get("primitive")):
        return []
    if workpoint.get("bytes_budget_by_resource") != request.get("bytes_budget_by_resource"):
        return []
    path_ids = workpoint.get("resource_path_ids", [])
    registry = mapping.get("physical", {}).get("directed_paths", {})
    if len(path_ids) != len(paths) or len(set(path_ids)) != len(path_ids):
        return []
    for path_id, path, incidence in zip(path_ids, paths, workpoint["resource_incidence"]):
        registered = registry.get(path_id, {})
        if (_path_identity(registered) != _path_identity(path)
                or set(registered.get("resource_ids", [])) != set(incidence)
                or not registered.get("source_refs")
                or request.get("runtime_config_id") not in registered.get("runtime_config_ids", [])):
            return []
    fingerprint = mapping.get("runtime_configs", {}).get(request.get("runtime_config_id"), {}).get("fingerprint")
    contracts = mapping.get("shared_resource_contracts", {})
    incidence = workpoint["resource_incidence"]
    shared = {rid for ids in incidence for rid in ids if sum(rid in row for row in incidence) > 1}
    admitted = []
    for row in mapping.get("measurements", []):
        try:
            age = now - _timestamp(row["measured_at"])
        except (KeyError, ValueError, TypeError):
            continue
        rates = row.get("per_path_rates_Bps", [])
        if (row.get("primitive") != request.get("primitive")
                or row.get("runtime_config_id") != request.get("runtime_config_id")
                or not fingerprint or row.get("runtime_config_fingerprint") != fingerprint
                or (require_profile and row.get("admission", {}).get("current_profile_eligible") is not True)
                or row.get("status") not in (("verified_current",) if require_profile
                                             else ("verified_current", "observed_shared_load"))
                or not row.get("source_refs")
                or row.get("timing", {}).get("planning_compatible") is not True
                or not 0 <= age <= max_age_s
                or row.get("availability_context_id") != mapping["availability"].get("context_id")
                or [_path_identity(p) for p in row.get("directed_paths", [])]
                   != [_path_identity(p) for p in paths]
                or row.get("workpoint") != workpoint
                or len(rates) != len(paths)
                or any(isinstance(r, bool) or not isinstance(r, (int, float))
                       or not math.isfinite(r) or r <= 0 for r in rates)):
            continue
        if require_profile and any(contracts.get(rid, {}).get("mode") != "finite_measured_workpoints"
               or row.get("id") not in contracts[rid].get("measurement_ids", [])
               or not contracts[rid].get("source_refs") for rid in shared):
            continue
        admitted.append(row)
    return admitted


def validate_resource_map(mapping):
    errors = []
    if mapping.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version")
    required = ("physical", "runtime_configs", "availability", "measurements", "planning_readiness")
    for key in required:
        if key not in mapping:
            errors.append("missing " + key)
    if errors:
        return {"passed": False, "errors": errors}
    registry = mapping["physical"].get("resources", {})
    fabric = mapping["physical"].get("fabric", {})
    for field in ("internal_topology", "capacity_Bps", "sharing_interference"):
        if fabric.get(field) is not None and not fabric.get(field + "_evidence_refs"):
            errors.append("fabric " + field + " lacks direct evidence")
    pci_ids, ordinals = set(), set()
    for rid, row in registry.items():
        if row.get("id") != rid:
            errors.append("identity key mismatch " + rid)
        if row.get("bdf"):
            identity = (row.get("node"), row["bdf"])
            if identity in pci_ids:
                errors.append("duplicate node/BDF " + rid)
            pci_ids.add(identity)
        if row.get("kind") == "gpu":
            ordinal = (row.get("node"), row.get("logical_gpu"))
            if ordinal in ordinals:
                errors.append("duplicate GPU logical ordinal " + rid)
            ordinals.add(ordinal)
        chain = row.get("pci_chain_leaf_to_root", [])
        if row.get("kind") in ("gpu", "hca") and (not chain or chain[0] != rid):
            errors.append("PCI chain must begin with device " + rid)
        for parent in row.get("pci_chain_leaf_to_root", []):
            if parent not in registry:
                errors.append("unknown PCI identity " + parent)
            elif registry[parent].get("node") != row.get("node"):
                errors.append("cross-node PCI ancestry " + rid)
        if row.get("numa_id") is not None and row["numa_id"] not in registry:
            errors.append("unknown NUMA identity " + rid)
        cap = row.get("capacity_Bps")
        if cap is not None and (isinstance(cap, bool) or not isinstance(cap, (int, float)) or not math.isfinite(cap) or cap <= 0):
            errors.append("invalid measured capacity " + rid)
        if cap is not None and not row.get("capacity_evidence_ref"):
            errors.append("capacity has no measurement evidence " + rid)
    for rid in mapping["availability"].get("resources", {}):
        if rid not in registry:
            errors.append("availability identity absent " + rid)
    for rid, contract in mapping.get("shared_resource_contracts", {}).items():
        if rid not in registry or contract.get("resource_id") != rid:
            errors.append("shared contract identity absent/mismatch " + rid)
        if (contract.get("mode") != "finite_measured_workpoints"
                or not contract.get("source_refs") or not contract.get("measurement_ids")):
            errors.append("shared contract lacks finite measurement evidence " + rid)
        if contract.get("capacity_Bps") is not None:
            errors.append("finite workpoint cannot declare hardware capacity " + rid)
    for path_id, path in mapping["physical"].get("directed_paths", {}).items():
        members = path.get("resource_ids", [])
        if (not members or len(members) != len(set(members)) or any(rid not in registry for rid in members)
                or path.get("src") not in members or path.get("dst") not in members
                or not path.get("source_refs")):
            errors.append("directed physical path lacks exact resource evidence " + path_id)
    return {"passed": not errors, "errors": errors}


def planner_readiness(mapping, request=None, *, now=None, max_age_s=300):
    """Require fresh ownership, explicit resource budgets and matching current rate evidence."""
    now = datetime.now(timezone.utc).timestamp() if now is None else _timestamp(now)
    if not math.isfinite(max_age_s) or max_age_s <= 0:
        raise ValueError("invalid maximum age")
    check = validate_resource_map(mapping)
    structural = check["passed"]
    reasons = list(check["errors"])
    runtime_reasons, performance_reasons = [], []
    request = request or {}
    ids = request.get("resource_ids", [])
    if not ids:
        runtime_reasons.append("no explicit requested resources")
    if len(ids) != len(set(ids)):
        runtime_reasons.append("duplicate requested resource identity")
    registry = mapping.get("physical", {}).get("resources", {})
    config_id = request.get("runtime_config_id")
    config = mapping.get("runtime_configs", {}).get(config_id, {})
    if not config or not config.get("runtime_observed"):
        runtime_reasons.append("runtime config has no actual runtime evidence")
    if any(rid not in config.get("observed_resource_ids", []) for rid in ids):
        runtime_reasons.append("requested resource outside observed runtime scope")
    samples = mapping.get("availability", {}).get("resources", {})
    budgets = request.get("bytes_budget_by_resource", {})
    for rid in ids:
        if rid not in registry:
            runtime_reasons.append("unknown requested resource " + rid)
            continue
        row = samples.get(rid, {})
        node = mapping["physical"]["nodes"].get(registry[rid].get("node"), {})
        operational = mapping.get("current_operational_status", {}).get("nodes", {}).get(registry[rid].get("node"))
        if operational is not None:
            if operational.get("control_reachable") is not True:
                runtime_reasons.append("control reachability unconfirmed " + registry[rid]["node"])
            if operational.get("own_probe_cleanup_confirmed") is not True:
                runtime_reasons.append("own probe cleanup unconfirmed " + registry[rid]["node"])
        try:
            age = now - _timestamp(row["sampled_at"])
            identity_age = now - _timestamp(row["owner_sampled_at"])
            if not 0 <= age <= max_age_s or not 0 <= identity_age <= max_age_s:
                runtime_reasons.append("expired resource/owner sample " + rid)
        except (KeyError, ValueError, TypeError):
            runtime_reasons.append("missing resource/owner timestamp " + rid)
        if row.get("boot_id") != node.get("boot_id"):
            runtime_reasons.append("resource sample boot mismatch " + rid)
        if row.get("owner_inventory_complete") is not True:
            runtime_reasons.append("incomplete owner inventory " + rid)
        if row.get("unknown_owners"):
            runtime_reasons.append("unknown active owner " + rid)
        if row.get("external_active_owners"):
            runtime_reasons.append("active external owners " + rid)
        if row.get("exclusive") is not True:
            runtime_reasons.append("resource is nonexclusive " + rid)
        authorization = row.get("authorized_budget_bytes")
        amount = budgets.get(rid)
        if (type(authorization) is not int or authorization < 0 or not row.get("budget_authorization_ref")
                or type(amount) is not int or amount < 0 or amount > authorization):
            runtime_reasons.append("missing or insufficient authorized budget " + rid)
        if type(amount) is int and (row.get("free_bytes") is None or amount > row["free_bytes"]):
            runtime_reasons.append("missing or insufficient sampled free capacity " + rid)
        if row.get("permitted_for_task") is not True:
            runtime_reasons.append("resource outside task permission " + rid)
    paths = request.get("directed_paths", [])
    if not paths or not request.get("primitive"):
        performance_reasons.append("no explicit directed paths/primitive")
    joint = _admitted_joint(mapping, request, now, max_age_s) if structural and len(paths) > 1 else []
    if len({_path_identity(p) for p in paths}) != len(paths):
        performance_reasons.append("duplicate directed path identity")
    if "workpoint" in request and not _workpoint_valid(request["workpoint"], paths, ids, request.get("primitive")):
        performance_reasons.append("invalid finite workload/resource incidence")
    if len(paths) > 1 and not joint:
        performance_reasons.append("no current matching finite multi-path workpoint; aggregate rates are not hardware capacities")
    for path in paths:
        admitted = joint if len(paths) > 1 else (_admitted(mapping, request, path, now, max_age_s) if structural else [])
        if not admitted:
            performance_reasons.append("no current matching admitted rate " + json.dumps(path, sort_keys=True))
        if path.get("src") not in ids or path.get("dst") not in ids:
            performance_reasons.append("path endpoints outside explicit resource scope")
    reasons.extend(runtime_reasons + performance_reasons)
    reasons = list(dict.fromkeys(reasons))
    return {"structure_ready": structural, "runtime_ready": structural and not runtime_reasons,
            "performance_ready": structural and not runtime_reasons and not performance_reasons,
            "ready": structural and not reasons, "reasons": reasons}


def planning_input(mapping, request, *, now=None, max_age_s=300):
    now = datetime.now(timezone.utc).timestamp() if now is None else _timestamp(now)
    readiness = planner_readiness(mapping, request, now=now, max_age_s=max_age_s)
    if not readiness["ready"]:
        raise PlanningNotReady(readiness)
    ids = request["resource_ids"]
    result = {"schema_version": SCHEMA_VERSION, "resources": {rid: resource(mapping, rid) for rid in ids},
            "budgets_bytes": copy.deepcopy(request["bytes_budget_by_resource"]),
            "runtime_config": copy.deepcopy(mapping["runtime_configs"][request["runtime_config_id"]]),
            "directed_path_evidence": ([_admitted(mapping, request, path, now, max_age_s)
                                        for path in request["directed_paths"]]
                                       if len(request["directed_paths"]) == 1 else []),
            "readiness": readiness}
    if len(request["directed_paths"]) > 1:
        result.update(joint_workpoint_evidence=copy.deepcopy(_admitted_joint(mapping, request, now, max_age_s)),
                      workpoint=copy.deepcopy(request["workpoint"]),
                      shared_resource_contracts=copy.deepcopy({rid: contract for rid, contract in
                          mapping.get("shared_resource_contracts", {}).items() if rid in ids}),
                      evidence_scope="exact finite workpoint only; no independent path or hardware capacities")
    return result
