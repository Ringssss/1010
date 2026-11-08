"""Use the four idle node27 GPUs when remote nodes are occupied; no cross-node claim."""
import argparse
from pathlib import Path
import resource

from run_managed import ManagedRun, ROOT
from run_system import NODES


class LocalManagedRun(ManagedRun):
    source_workers = [2, 3]
    replica_workers = [2, 3]
    target_workers = [0, 1]
    target_node = "node27"

    def isolated_containers(self):
        self.event("local_validation_scope", physical_gpus=[0, 1, 4, 5],
                   reason="node28 and node29 GPUs occupied; remote validation remains pending")


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    info = dict(NODES["node27"], gpus=[0, 1, 4, 5])
    NODES.clear(); NODES["node27"] = info
    parser = argparse.ArgumentParser(); parser.add_argument("--run", required=True)
    parser.add_argument("--fixture-only", action="store_true")
    parser.add_argument("--port-base", type=int, default=46100)
    parser.add_argument("--model", default="/data/node28/shared/zhujianian/Qwen3.6-35B-A3B")
    parser.set_defaults(worker_serialize=0, handoff_poll=.01, development="v0.1.23.3",
        mesh="node27:0-1,node27:4-5", replica_workers=[2, 3], source_workers=[2, 3],
        result_root=str(ROOT/"codex_coding/results/v0.1.23.3/task2"), source_directories=[str(Path(__file__).parent)])
    LocalManagedRun(parser.parse_args()).run()
