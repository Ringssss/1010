"""Registered-evidence fixture: real Qwen TP2 extents on the frozen two-node topology.

Loads the archived exact-extent path model and launch plan. Emits disjoint,
byte-exact extent replicas so that any selection of them can be turned into a
native 66-operation plan whose workload fingerprint equals the frozen one.
No fitting, no interpolation, no hardware access.
"""
import json
from pathlib import Path

RESULT = Path(__file__).resolve().parents[5] / "results/v0.1.23.3/task2/online_cost"
MODEL_PATH = (RESULT / "outer_ops_source_bundle_r01/payload/codex_coding/results/"
              "v0.1.23.3/task2/online_cost/execution_runtime_online_simple_two_node_r02/"
              "phase_calibrate/checkpoint_path_model.json")
LAUNCH_PATH = RESULT / "outer_ops_source_bundle_r01/source_launch_plan.unbound.json"

GiB = 1 << 30
CHUNK = 32 << 20
TAIL = 805120688

# Registered physical roles of the single-sender workflow.
ROLES = (
    dict(worker=0, node="node27", gpu=4, numa=4, bdf="0000:85:00.0"),
    dict(worker=1, node="node27", gpu=5, numa=7, bdf="0000:b5:00.0"),
    dict(worker=2, node="node30", gpu=4, numa=4, bdf="0000:85:00.0"),
)
WARM_PAIRS = ["0:2", "1:2"]


def load():
    model = json.loads(MODEL_PATH.read_text())
    launch = json.loads(LAUNCH_PATH.read_text())
    return model, launch


def workers(session="CPU_decision_value_r01"):
    return [dict(worker=r["worker"], node=r["node"], gpu=r["gpu"], numa=r["numa"],
                 session=session, epoch=f"dv_epoch_{r['worker']}",
                 capabilities=["local_dram", "local_hbm", "network", "peer", "local_ssd"])
            for r in ROLES]


def ranks(model):
    """Rank byte sizes and content identities, taken from the frozen workload."""
    return [(t["tp"], t["identity"], t["n"]) for t in model["context"]["full_model_plan_workload"]["targets"]]


def extents(n):
    """Yield (index, off, nbytes) for the registered 1 GiB-plus-tail split."""
    index = 0
    for off in range(0, n, GiB):
        yield index, off, min(GiB, n - off)
        index += 1


def class_of(tier, owner, rank):
    """Map a (tier, owner) replica to the registered concrete path family."""
    if tier == "hbm":
        if owner != rank:
            raise ValueError("hbm replica must live on the owning rank's card")
        return "hbm"
    if tier == "dram":
        return "remote" if owner == 2 else "local"
    raise ValueError("unregistered tier: " + tier)


def path_for(tier, owner, rank, model):
    """Concrete registered path key for a (tier, owner) replica of a rank extent."""
    if tier == "hbm":
        key = f"{owner}:hbm:{(4, 7)[owner]}->{owner}"
    elif tier == "dram":
        key = f"{owner}:dram:{(4, 7)[owner]}->{rank}" if owner != 2 else f"2:dram:{(4, 7)[rank]}->{rank}"
    else:
        raise ValueError("unregistered tier: " + tier)
    if key not in model["context"]["path_context"]:
        raise ValueError("path outside registered domain: " + key)
    return key


def base_replicas(model):
    """Complete DRAM base copy on the single remote sender (worker 2).

    The helper keeps rank 0 on NUMA 4 and rank 1 on NUMA 7, as registered by
    `remote_source_numa_by_rank`; both are distinct concrete paths.
    """
    rows = []
    for rank, identity, n in ranks(model):
        numa = ROLES[rank]["numa"]
        for index, off, size in extents(n):
            rows.append(dict(id=f"base/r{rank}/e{index}", identity=identity, tp=rank,
                             off=off, n=size, worker=2, tier="dram", numa=numa,
                             epoch="dv_epoch_2", state="VALID", generation=1, leases=0,
                             storage_node="node30"))
    return rows


def candidate_replicas(model, tiers=("hbm", "local")):
    """Every legal incremental copy, one per extent per class.

    hbm   -> the owning rank's own card
    local -> the owning rank's own NUMA on node27
    remote-> already present in the base, never a candidate
    """
    rows = []
    for rank, identity, n in ranks(model):
        own_numa = ROLES[rank]["numa"]
        for index, off, size in extents(n):
            if "hbm" in tiers:
                rows.append(dict(id=f"cache/r{rank}/e{index}/hbm", identity=identity, tp=rank,
                                 off=off, n=size, worker=rank, tier="hbm", numa=(4, 7)[rank],
                                 epoch=f"dv_epoch_{rank}", state="VALID", generation=1, leases=0,
                                 index=index, class_rank=0))
            if "local" in tiers:
                rows.append(dict(id=f"cache/r{rank}/e{index}/local", identity=identity, tp=rank,
                                 off=off, n=size, worker=rank, tier="dram", numa=own_numa,
                                 epoch=f"dv_epoch_{rank}", state="VALID", generation=1, leases=0,
                                 index=index, class_rank=1))
    return rows


def budgets(launch, hbm_per_card=None):
    """Registered capacity ledger; hbm_per_card overrides only for explicit sweeps."""
    cap = launch["nodes"]["node27"]["per_selected_gpu_hbm_cap_bytes"]
    persistent = launch["persistent_local_hbm_bytes_by_rank"][0]
    free = cap - persistent
    if hbm_per_card is None:
        hbm_per_card = free
    if not 0 <= hbm_per_card <= free:
        raise ValueError("hbm override outside physically available range")
    dram27 = launch["nodes"]["node27"]["task_dram_cap_bytes"]
    dram30 = launch["nodes"]["node30"]["task_dram_cap_bytes"]
    location = {"0:hbm": hbm_per_card, "1:hbm": hbm_per_card,
                "2:hbm": 0,                    # helper card keeps only its 2 GiB reserve
                "0:dram": dram27 // 2, "1:dram": dram27 // 2,
                "2:dram": dram30}
    return dict(per_card_hbm_free=hbm_per_card, registered_per_card_hbm_cap=cap,
                persistent_per_card=persistent, location_budgets=location,
                tier_budgets=dict(hbm=2 * hbm_per_card, dram=dram27 + dram30))
