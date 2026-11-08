"""Freeze one scenario contract: candidates, budget, targets, and the domain check.

The contract is the only thing all six strategies are allowed to see. It is
written once, hashed, and never regenerated after a result is observed.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASK2 = HERE.parents[1]
ROOT = HERE.parents[5]
sys.path.insert(0, str(ROOT / "main_lib"))
sys.path.insert(0, str(TASK2 / "online_cost/model"))
sys.path.insert(0, str(TASK2 / "online_cost/execution"))
sys.path.insert(0, str(HERE.parent / "case"))

from registered_fixture import (GiB, MODEL_PATH, ROLES, WARM_PAIRS, base_replicas,
                               budgets, candidate_replicas, load, ranks)
from scorer import digest, profile_from_points, targets_of


def contract(hbm_per_card_gib=None, label="registered"):
    model, launch = load()
    b = budgets(launch, None if hbm_per_card_gib is None
                else int(round(hbm_per_card_gib * GiB)))
    ranked = ranks(model)
    body = dict(
        schema="decision_value_contract_r01",
        development="v0.1.23.3/task2",
        label=label,
        model=dict(name="Qwen TP2", total_bytes=sum(n for _, _, n in ranked),
                   ranks=[dict(tp=tp, identity=identity, n=n) for tp, identity, n in ranked],
                   extent_bytes=GiB, tail_bytes=ranked[0][2] - (ranked[0][2] // GiB) * GiB),
        topology=dict(roles=[dict(r) for r in ROLES], warm_pairs=list(WARM_PAIRS),
                      targets=targets_of(model), single_sender=2),
        budget=dict(per_card_hbm_free_bytes=b["per_card_hbm_free"],
                    registered_per_card_hbm_cap_bytes=b["registered_per_card_hbm_cap"],
                    persistent_per_card_bytes=b["persistent_per_card"],
                    location_budgets=dict(b["location_budgets"]),
                    tier_budgets=dict(b["tier_budgets"])),
        base_replica_ids=[r["id"] for r in base_replicas(model)],
        candidate_ids=[c["id"] for c in candidate_replicas(model)],
        registered_paths=sorted(model["point_costs"]),
        registered_sizes=sorted(model["point_costs"][sorted(model["point_costs"])[0]]),
        outer_residual_s=0.4235804238204,
        model_source=str(MODEL_PATH.relative_to(ROOT)),
        routing_profile=profile_from_points(model),
    )
    body["sha256"] = digest(body)
    return body


if __name__ == "__main__":
    print(json.dumps(contract(), indent=1))
