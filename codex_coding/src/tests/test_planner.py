"""Planner sanity on a hand-solvable toy: python -m pytest tests/test_planner.py"""
from fluidckpt.model.fluid import maxmin_rates, simulate
from fluidckpt.planner.plans import (DemandClass, Problem, plan_fastest_source, plan_lp, plan_nvme_only,
                                     linear_regime, plan_resource_greedy, predict_makespan)

GB = 10 ** 9


class ToyModel:
    """GPU g: DRAM (numa 0) -> g shares one 10 GB/s memory port; NVMe 7 GB/s shared; P2P 50 GB/s."""
    gpu_numa = {0: 0, 1: 0}
    capacity = {"mem_rd:0": 10.0, "nvme_rd": 7.0}

    def cap_of(self, r):
        return self.capacity.get(r, float("inf"))

    def full_capacity(self, flows):
        return dict(self.capacity)

    def flows(self, specs, joint=True):
        out = []
        for s in specs:
            if s["kind"] == "h2d":
                out.append((10.0, ["mem_rd:0"]))
            elif s["kind"] == "nvme2gpu":
                out.append((7.0, ["nvme_rd"]))
            else:
                out.append((50.0, [f"out:{s['src']}"]))
        return out


def test_maxmin_shares_bottleneck():
    assert maxmin_rates([(10, ["a"]), (10, ["a"]), (3, [])], {"a": 8}) == [4.0, 4.0, 3.0]


def test_simulate_reallocates_after_finish():
    fin = simulate([(10, ["a"], 4 * GB), (10, ["a"], 8 * GB)], {"a": 8})
    assert abs(fin[0] - 1.0) < 1e-9 and abs(fin[1] - 1.5) < 1e-9


def test_lp_splits_across_dram_and_nvme():
    m = ToyModel()
    classes = [DemandClass(g, 17 * GB, [{"tier": "dram", "numa": 0}, {"tier": "nvme"}]) for g in (0, 1)]
    prob = Problem(classes, m)
    lp = plan_lp(prob, quantum=1)
    # 34 GB over DRAM (10) + NVMe (7) = 2.0 s; fastest-source puts all on DRAM = 3.4 s
    assert abs(lp.lp_bound_s - 2.0) < 1e-6
    assert abs(predict_makespan(lp, m) - 2.0) < 1e-3
    assert abs(predict_makespan(plan_fastest_source(prob), m) - 3.4) < 1e-6
    assert abs(predict_makespan(plan_nvme_only(prob), m) - 34 / 7) < 1e-6
    assert predict_makespan(plan_resource_greedy(prob, chunk=GB), m) < 2.2


def test_resident_class_needs_no_transfer_and_mincost_avoids_p2p():
    m = ToyModel()
    classes = [DemandClass(0, 5 * GB, [{"tier": "hbm", "gpu": 0}]),
               DemandClass(1, 4 * GB, [{"tier": "hbm", "gpu": 0}, {"tier": "dram", "numa": 0}, {"tier": "nvme"}])]
    prob = Problem(classes, m)
    assert prob.cands[0] is None
    fast = plan_lp(prob, quantum=1)
    cheap = plan_lp(prob, quantum=1, cost=lambda s: 1.0 if s["kind"] == "p2p" else 0.0, slack=10.0)  # 10x time budget: no P2P needed
    assert all(p["kind"] != "p2p" for p in cheap.paths)
    assert fast.lp_bound_s < 0.1


def test_linear_regime_keeps_root_ports_single_node():
    gpu_numa = {0: 0, 1: 3, 2: 2}  # node27; the NVMe shares GPU 0's root port and sits on NUMA 0
    assert linear_regime({"kind": "p2p", "src": 1, "dst": 2}, gpu_numa)
    assert linear_regime({"kind": "h2d", "numa": 3, "gpu": 1}, gpu_numa)
    assert not linear_regime({"kind": "h2d", "numa": 0, "gpu": 1}, gpu_numa)  # NUMA 0 would feed two ports
    assert linear_regime({"kind": "nvme2gpu", "numa": 0, "gpu": 0}, gpu_numa)
    assert not linear_regime({"kind": "nvme2gpu", "numa": 2, "gpu": 2}, gpu_numa)  # NVMe port writes node 2
