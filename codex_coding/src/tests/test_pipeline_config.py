"""Planner fallbacks and executor selection must remain explicit and fail before RPC."""
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from fluidckpt.cluster.control import Cluster
from fluidckpt.cluster.planning import restore_plan, placement_plan
from test_cluster import PROFILE, workers, replica, target


def test_lp_fallback_records_requested_and_actual_strategy():
    with patch("scipy.optimize.linprog", return_value=SimpleNamespace(success=False, message="injected time limit")):
        plan = restore_plan([replica()], [target()], workers(), PROFILE, "resource_lp")
        req = dict(replica("new"), locations=[{"worker": 4, "tier": "dram"}])
        placement = placement_plan([replica()], [req], workers(), PROFILE, "joint_greedy",
                                   [{"targets": [target()]}], extra_budget=1 << 30)
    assert plan["requested_strategy"] == "resource_lp" and plan["strategy"] == "nearest"
    assert plan["fallback_reason"] == "injected time limit"
    assert placement["evaluation_actual_strategy_counts"]["nearest"] > 0
    assert placement["evaluation_fallback_reasons"] == ["injected time limit"]


def test_bad_executor_does_not_start_a_transaction():
    cluster = Cluster(["unused"], PROFILE)
    with patch.object(cluster, "inventory", side_effect=AssertionError("must not issue RPC")):
        with pytest.raises(ValueError): cluster.transfer({"executor": "unknown"})
    assert not cluster.active


def test_bad_envelope_does_not_leave_active_state():
    cluster = Cluster(["endpoint"], PROFILE)
    bad = {"targets": [{}], "ops": [], "control_context": {"endpoints": ["endpoint"], "epochs": {"0": "epoch"}}}
    with pytest.raises(KeyError): cluster.transfer(bad)
    assert not cluster.active
