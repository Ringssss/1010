"""Read-only node inventory and post-exit artifacts for owned probe ranks."""
import argparse
import json
from pathlib import Path
import resource
import sys

HERE = Path(__file__).resolve().parent
DIAGNOSIS = HERE.parents[1] / "topology_scenarios/diagnosis"
sys.path.insert(0, str(DIAGNOSIS))
import read_inventory as previous
from recollect import remote_log

RESULT_ROOT = previous.base.ROOT / "codex_coding/results/v0.1.23.3/task2/topology_alignment"


def owned_directory(path):
    path = Path(path).resolve()
    if RESULT_ROOT not in path.parents or not path.name.startswith("probe_"):
        raise ValueError("artifact must belong to a topology_alignment/probe_* run")
    return path


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--node-snapshot", choices=("node27", "node29"))
    parser.add_argument("--owned-state", type=Path)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--hca", default="shca_0")
    parser.add_argument("--ifname", default="ib0")
    args = parser.parse_args()
    if args.artifact:
        owned_directory(args.artifact.parent)
        if args.pid is None:
            parser.error("--pid required")
        if args.artifact.exists():
            value = dict(remote_log(args.artifact, args.pid), exists=True)
        else:
            value = {"path": str(args.artifact), "exists": False,
                     "process": previous.base.proc_state(args.pid)}
        print(json.dumps(value))
    elif args.owned_state:
        directory = owned_directory(args.owned_state)
        rows = []
        for path in sorted(directory.glob("rank_*.process.pid.json")):
            recorded = json.loads(path.read_text())
            rows.append({"registered": recorded, "actual": previous.base.proc_state(recorded["pid"])})
        print(json.dumps(rows))
    elif args.node_snapshot:
        value = previous.base.node_snapshot(args.session, args.node_snapshot, args.hca, args.ifname)
        value["diagnosis_nic_cpu_facts"] = previous.facts(args.session, args.node_snapshot)
        print(json.dumps(value))
    else:
        parser.error("choose a read-only operation")


if __name__ == "__main__":
    main()
