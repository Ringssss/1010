"""BlitzScale source/cache and TP distribution policy, using the HIP keeper backend.

Algorithm origin is pinned below. This adapter supports one complete TP destination;
it does not implement live scaling or multi-destination chain pipelining.
"""
from collections import Counter
from dataclasses import dataclass, field
import copy
from pathlib import Path
import time

from fluidckpt.benchmarks.basic_scenarios import _ledger
from fluidckpt.cluster.path_catalog import check_profile_evidence
from fluidckpt.cluster.planning import covers, extent, path_for, validate_plan
from fluidckpt.cluster.wire import planning_context

UPSTREAM_SHA = "594e91127714349c183d935ac68d7b0eb63fc1ed"
IDENTITY = "BlitzScale distribution mechanism HIP adaptation"
ORIGIN = {
    "commit": UPSTREAM_SHA,
    "cache": "router_v2/src/replica/steersman/mod.rs:103-155,165-211,263-289,340-357",
    "source": "router_v2/src/replica/cybernetics/exec_blitz.rs:1200-1244",
    "distribution": "router_v2/src/replica/cybernetics/exec_blitz.rs:2024-2157",
    "tp_chain": "src/service/service.cc:358-411",
}


@dataclass
class CacheEntry:
    ranks: set = field(default_factory=set)
    timestamp: float = 0.0


class SteersmanPort:
    """Port of the frozen cache, first-local-source and semaphore algorithms.

    Frozen Rust HashSet iteration is unspecified; insertion order is our explicit
    deterministic tie rule. A rank here is a complete TP replica's leader, because
    its members contain different bytes and must be admitted as one logical group.
    """
    def __init__(self, ttl_ms=60000, clock=time.monotonic):
        if type(ttl_ms) is not int or ttl_ms < 0:
            raise ValueError("TTL must be a nonnegative integer in milliseconds")
        self.ttl_ms, self.clock = ttl_ms, clock
        self.rank_to_machine, self.rank_nvlink_sem = {}, {}
        self.machine_cached_models = {}
        self.rank_order = []

    def register_replica(self, rank, machine):
        if rank in self.rank_to_machine:
            if self.rank_to_machine[rank] != machine:
                raise ValueError("replica leader changed machine")
            return
        self.rank_to_machine[rank] = machine
        self.rank_nvlink_sem[rank] = 0
        self.rank_order.append(rank)

    def record_model_loaded(self, rank, model):
        machine = self.rank_to_machine[rank]
        entry = self.machine_cached_models.setdefault(machine, {}).setdefault(model, CacheEntry())
        entry.ranks.add(rank)
        entry.timestamp = self.clock()

    def record_model_unloaded(self, rank, model):
        entry = self.machine_cached_models.get(self.rank_to_machine[rank], {}).get(model)
        if entry:
            entry.ranks.discard(rank)
        # Upstream preserves the last load timestamp on unload.

    def machine_has_cache(self, rank, model):
        entry = self.machine_cached_models.get(self.rank_to_machine[rank], {}).get(model)
        return bool(entry and (entry.ranks or (self.clock()-entry.timestamp)*1000 < self.ttl_ms))

    def find_one_rank_within_cache(self, machine, model):
        entry = self.machine_cached_models.get(machine, {}).get(model)
        if entry and (entry.ranks or (self.clock()-entry.timestamp)*1000 < self.ttl_ms):
            return next((rank for rank in self.rank_order if rank in entry.ranks
                         and self.rank_nvlink_sem[rank] == 0), None)
        return None

    def assign_nvlink_chains(self, model, inactive):
        first, edges = {}, []
        for rank in inactive:
            machine = self.rank_to_machine[rank]
            source = first.get(machine)
            if source is None:
                source = self.find_one_rank_within_cache(machine, model)
                if source is not None:
                    first[machine] = source
            edges.append((source, rank))
        return edges

    def wait_replica_nvlink(self, rank, count):
        if self.rank_nvlink_sem[rank] != 0 or count <= 0:
            raise ValueError("source distribution semaphore busy")
        self.rank_nvlink_sem[rank] = count

    def post_replica_nvlink(self, rank):
        if self.rank_nvlink_sem[rank] <= 0:
            raise ValueError("source distribution semaphore underflow")
        self.rank_nvlink_sem[rank] -= 1


def rdma_tp_chains(source_ranks, destination_groups):
    """Port RdmaBroadcast's dst_ranks.begin()+tp_rank, it+=tp_size loop."""
    tp = len(source_ranks)
    if not tp or any(len(group) != tp for group in destination_groups):
        raise ValueError("incomplete TP group")
    if len(set(source_ranks)) != tp or any(len(set(group)) != tp for group in destination_groups):
        raise ValueError("duplicate TP worker")
    flat = [rank for group in destination_groups for rank in group]
    if len(set(source_ranks + flat)) != len(source_ranks + flat):
        raise ValueError("source and destination groups overlap")
    return [[source_ranks[rank], *flat[rank::tp]] for rank in range(tp)]


def span_max_rdma_sending_replicas(normal_decode, shutting_decode, shutted_null, rdma_states):
    """Frozen default branch, without the optional unidirect feature."""
    return [rank for rank in [*normal_decode, *shutting_decode, *shutted_null]
            if rdma_states.get(rank, "Inactive") == "Inactive"]


class BlitzScaleAdapter:
    def __init__(self, ttl_ms=60000, clock=time.monotonic):
        self.clock = clock
        self.steersman = SteersmanPort(ttl_ms, clock)
        self.loaded = {}

    @staticmethod
    def method():
        return {"identity": "hardware_adaptation", "implementation": "implemented",
                "name": IDENTITY, "commit": UPSTREAM_SHA,
                "scope": "single complete TP target; cache/source/group distribution; no live serving"}

    def _groups(self, case, model_key, workers):
        rows_by_id = {row["id"]: row for row in case["replicas"]}
        groups, rejected = [], {}
        targets = case["targets"]
        for instance in case.get("active_instances", []) + case.get("cached_instances", []):
            ids = instance.get("replica_ids", [])
            rows = [rows_by_id[rid] for rid in ids if rid in rows_by_id]
            reason = None
            if not ids or len(ids) != len(set(ids)) or len(rows) != len(ids):
                reason = "missing_or_duplicate_group_members"
            elif any(row["state"] != "VALID" or row["tier"] != "hbm" for row in rows):
                reason = "group_not_complete_HBM"
            elif any(not any(r["tp"] == t["tp"] and r["identity"] == t["identity"] for t in targets) for r in rows):
                reason = "group_has_foreign_TP_content"
            elif any(not covers(rows, t["identity"], t["tp"], *extent(t)) for t in targets):
                reason = "group_not_complete_TP_content"
            elif any(len({r["worker"] for r in rows if r["tp"] == t["tp"]}) != 1 for t in targets):
                reason = "TP_rank_has_multiple_source_workers"
            elif len({workers[r["worker"]]["node"] for r in rows}) != 1:
                reason = "logical_replica_spans_hosts"
            elif set(r["worker"] for r in rows) & set(t["worker"] for t in targets):
                reason = "source_is_target"
            elif instance in case.get("active_instances", []) and (
                    instance.get("survival_evidence", {}).get("alive_before") is not True
                    or instance.get("survival_evidence", {}).get("protected_during_copy") is not True
                    or not Path(instance.get("survival_evidence", {}).get("path", "")).is_file()):
                reason = "source_survival_or_protection_missing"
            elif any(g["leader"] == next(r["worker"] for r in rows if r["tp"] == 0) for g in groups):
                reason = "duplicate_logical_source_on_same_devices"
            if reason:
                rejected[instance["logical_id"]] = reason
                continue
            ranks = [next(r["worker"] for r in rows if r["tp"] == t["tp"]) for t in targets]
            leader = ranks[0]
            self.steersman.register_replica(leader, workers[leader]["node"])
            signature = tuple(sorted((r["id"], r["generation"], r.get("epoch")) for r in rows))
            if self.loaded.get(leader) != (model_key, signature):
                if leader in self.loaded:
                    self.steersman.record_model_unloaded(leader, self.loaded[leader][0])
                self.steersman.record_model_loaded(leader, model_key)
                self.loaded[leader] = (model_key, signature)
            groups.append({"leader": leader, "ranks": ranks, "rows": rows, "instance": instance})
        for leader in set(self.loaded)-set(g["leader"] for g in groups):
            self.steersman.record_model_unloaded(leader, self.loaded.pop(leader)[0])
        return groups, rejected

    def plan(self, case):
        started = self.clock()
        if any(case.get("format_contract", {}).get(key) is None for key in (
                "tp", "model_version", "dtype", "quantization", "processed_snapshot_id")):
            raise ValueError("processed format contract incomplete")
        if len({r["id"] for r in case["replicas"]}) != len(case["replicas"]):
            raise ValueError("duplicate source replica ID")
        if len({w["worker"] for w in case["workers"]}) != len(case["workers"]):
            raise ValueError("duplicate worker ID")
        workers = {row["worker"]: row for row in case["workers"]}
        targets = sorted(case["targets"], key=lambda row: row["tp"])
        tp = case["format_contract"]["tp"]
        if len(targets) != tp or [t["tp"] for t in targets] != list(range(tp)):
            raise ValueError("one complete TP destination required; multi-target chains not implemented")
        if len({workers[t["worker"]]["node"] for t in targets}) != 1:
            raise ValueError("target TP group must reside on one host")
        if len({t["worker"] for t in targets}) != tp:
            raise ValueError("one destination device per TP rank required")
        if any(t.get("tier", "hbm") != "hbm" for t in targets):
            raise ValueError("HBM targets required")
        check_profile_evidence(case["workers"], case["profile"])
        ledger = _ledger(case)
        budget = case["budget"]
        if (ledger["full_base_dram_bytes"] > budget["base_dram_bytes"]
                or ledger["extra_cache_hbm_bytes"]+ledger["ipc_owner_hbm_bytes"] > budget["extra_hbm_bytes"]
                or any(n > budget.get("location_extra_hbm_bytes", {}).get(loc, 0)
                       for loc, n in ledger["extra_location_bytes"].items())):
            raise ValueError("physical allocation budget exceeded")
        model_key = tuple((t["tp"], t["identity"], t.get("off", 0), t["n"]) for t in targets)
        policy_case = dict(case, targets=targets)
        groups, rejected = self._groups(policy_case, model_key, workers)
        destination = targets[0]["worker"]
        self.steersman.register_replica(destination, workers[destination]["node"])
        local = self.steersman.assign_nvlink_chains(model_key, [destination])[0][0]
        selected = next((g for g in groups if g["leader"] == local), None)
        mode = "local_cached_TP_group" if selected else None
        if selected is None:
            # Port span_max_rdma_sending_replicas without feature 'unidirect'.
            state_members = [[g["leader"] for g in groups if g["instance"].get("blitz_state", "Decode") == state]
                             for state in ("Decode", "ShuttingDecode", "ShuttedNull")]
            candidates = span_max_rdma_sending_replicas(*state_members,
                {g["leader"]: g["instance"].get("rdma_state", "Inactive") for g in groups})
            # Frozen rdma_bcast_scale_n_decode pops the source vector's last entry.
            if candidates:
                leader = candidates.pop()
                selected, mode = next(g for g in groups if g["leader"] == leader), "rdma_TP_chain_single_target"
        source_rows, fallback = [], None
        if selected:
            source_rows = selected["rows"]
            chains = rdma_tp_chains(selected["ranks"], [[t["worker"] for t in targets]])
            if any(not path_for(row, t, workers, case["profile"]) for row in source_rows
                   for t in targets if row["tp"] == t["tp"] and row["identity"] == t["identity"]):
                fallback = "selected_TP_group_has_unverified_path"
                source_rows, selected, mode = [], None, None
        else:
            fallback = "no_admissible_complete_HBM_TP_group"
        if not source_rows:
            raise ValueError("not_supported: " + fallback + "; CPU pool source policy has not been ported")
        ops, loads, capacities = [], Counter(), {}
        for t in targets:
            cursor, end = extent(t)
            rows = sorted((r for r in source_rows if r["tp"] == t["tp"] and r["identity"] == t["identity"]),
                          key=lambda r: (r.get("off", 0), r["id"]))
            for r in rows:
                begin, source_end = extent(r)
                if begin > cursor:
                    raise ValueError("source group has a coverage hole")
                n = min(end, source_end)-cursor
                if n <= 0:
                    continue
                path = path_for(r, t, workers, case["profile"])
                ops.append({"op_id": len(ops), "target": t["key"], "tp": t["tp"], "identity": t["identity"],
                            "off": cursor, "n": n, "source_offset": cursor-begin, "replica_id": r["id"],
                            "generation": r["generation"], "source_epoch": r.get("epoch"),
                            "src_w": r["worker"], "dst_w": t["worker"], "kind": path["kind"],
                            "path": path["path"], "resources": list(path["resources"])})
                for resource, cap in path["resources"].items():
                    loads[resource] += n
                    capacities[resource] = cap
                loads["path:"+path["path"]] += n
                capacities["path:"+path["path"]] = path["GBps"]
                cursor += n
                if cursor == end:
                    break
            if cursor != end:
                raise ValueError("incomplete destination coverage")
        plan = {"targets": copy.deepcopy(targets), "ops": ops, "strategy": "blitz_scale_tp_distribution",
                "requested_strategy": "blitz_scale_tp_distribution", "profile": case["profile"],
                "planning_s": self.clock()-started, "fallback_reason": fallback,
                "estimated_transfer_s": max(loads[key]/1e9/capacities[key] for key in loads),
                "bandwidth_lower_bound_s": None, "adaptation_identity": IDENTITY,
                "blitz": {"origin": ORIGIN, "source_mode": mode, "tp_chains": chains,
                          "source_group": selected["instance"]["logical_id"] if selected else None,
                          "source_leader": selected["leader"] if selected else None,
                          "source_ids": sorted({op["replica_id"] for op in ops}), "rejections": rejected,
                          "single_target_degenerates_to_p2p": True,
                          "enabled_policy_path": "impl_nvl local-cache priority then non-live impl_fast RDMA TP chain",
                          "cache_rank_projection": "complete-TP-replica leaders; per-device TP shards remain distinct",
                          "cache_load_timestamp": "first observed valid group generation; loaded groups remain valid beyond TTL",
                          "ttl_ms": self.steersman.ttl_ms,
                          "uncovered": ["CPU parameter pool fallback policy", "multi-target RDMA pipeline", "Tanz multi-lane matrix",
                                        "partial-layer live serving", "request/SLO scale controller",
                                        "SSD O_DIRECT loader", "CUDA/RDMA execution backend"],
                          "execution_backend": "existing HIP/RCCL keeper with generation-checked read leases",
                          "tie_rule": "input group order; local insertion order instead of Rust HashSet order"},
                "scope": "frozen BlitzScale cache/source/TP route port; one complete destination; weights-ready endpoint"}
        plan["resource_ledger"] = ledger
        validate_plan(plan)
        return plan

    def transfer(self, cluster, plan, **kwargs):
        leader = plan["blitz"]["source_leader"]
        if leader is not None:
            self.steersman.wait_replica_nvlink(leader, 1)
        try:
            result = cluster.transfer(plan, **kwargs)
            result["blitz_source_protection"] = {"leader": leader, "released": True,
                "physical_protection": "Cluster.prepare generation/epoch leases; Cluster.finish/abort releases leases",
                "planner_semaphore_scope": "distribution execute/publish; separate read leases retained through verification"}
            return result
        finally:
            if leader is not None:
                self.steersman.post_replica_nvlink(leader)


def bind_blitz_plan(plan, workers, endpoints):
    return dict(plan, control_context=planning_context(workers, endpoints), control_mode="compact", planner_rpc={})
