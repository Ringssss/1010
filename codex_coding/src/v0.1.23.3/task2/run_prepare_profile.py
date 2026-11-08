"""One instrumented replay of the two base locations; target preparation only."""
import argparse
from pathlib import Path
import resource

from run_base_location_idle import IdleBaseLocationRun, ROOT, NODES


class PrepareProfileRun(IdleBaseLocationRun):
    def start(self, node, name, command, env=None):
        if name.startswith('worker_'):
            env = dict(env or {}, FLUIDCKPT_PREPARE_PROFILE_DIR=str(self.out/'prepare_profiles'))
        return super().start(node, name, command, env)


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    keep = {'node27': dict(NODES['node27'], gpus=[6, 7], container='zjn-fluidckpt-task2-prepdiag-tools'),
            'node28': dict(NODES['node28'], gpus=[6, 7])}
    NODES.clear(); NODES.update(keep)
    parser = argparse.ArgumentParser(); parser.add_argument('--run', required=True)
    parser.add_argument('--port-base', type=int, default=20600)
    parser.add_argument('--server-port', type=int, default=31944)
    parser.add_argument('--model', default='/data/node28/shared/zhujianian/Qwen3.6-35B-A3B')
    parser.set_defaults(match_hca=False, inference_base_gpu=6, worker_serialize=0, handoff_poll=.01, fixture_only=False, development='v0.1.23.3',
        mesh='node27:6-7,node28:6-7', result_root=str(ROOT/'codex_coding/results/v0.1.23.3/task2'),
        source_directories=[str(Path(__file__).parent)])
    PrepareProfileRun(parser.parse_args()).run()
