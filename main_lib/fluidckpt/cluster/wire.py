"""Project planner inputs and per-worker envelopes without removing execution validation."""
from .planning import validate_plan


def project(row, fields):
    return {key: row[key] for key in fields if key in row}


def target_header(target):
    return dict(project(target, ("key", "worker", "identity", "tp", "n", "tier", "numa", "storage_node")), off=target.get("off", 0))


def planning_payload(replicas, targets, workers, profile, strategy):
    return {
        "replicas": [project(r, ("id", "worker", "identity", "tp", "off", "n", "tier", "numa",
                                  "state", "generation", "epoch", "storage_node")) for r in replicas],
        "targets": [target_header(t) for t in targets],
        "workers": [project(w, ("worker", "node", "gpu", "numa", "capabilities")) for w in workers],
        "profile": profile, "strategy": strategy,
    }


def bind_manifests(plan, targets):
    if plan["targets"] != [target_header(t) for t in targets]:
        raise ValueError("planner changed target identity, owner or extent")
    plan = dict(plan, targets=targets)
    validate_plan(plan)
    return plan


def worker_plan(plan, worker):
    # All participants still validate global byte coverage and see identical operation order.
    # Only the target owner maps storages and needs the full tensor layout.
    return dict(plan, targets=[t if t["worker"] == worker else
                {k: v for k, v in t.items() if k not in ("meta", "index")}
                for t in plan["targets"]])


def planning_context(workers, endpoints):
    if (len(workers) != len(endpoints) or [w["worker"] for w in workers] != list(range(len(endpoints)))
            or len({w["session"] for w in workers}) != 1):
        raise ValueError("invalid worker snapshot")
    return {"endpoints": list(endpoints), "session": workers[0]["session"],
            "epochs": {str(w["worker"]): w["epoch"] for w in workers}}


def checked_epochs(context, endpoints):
    if (context.get("endpoints") != list(endpoints)
            or set(context.get("epochs", {})) != {str(i) for i in range(len(endpoints))}
            or not all(isinstance(e, str) and e for e in context["epochs"].values())):
        raise ValueError("plan belongs to different endpoints or has an invalid worker snapshot")
    return dict(context["epochs"])
