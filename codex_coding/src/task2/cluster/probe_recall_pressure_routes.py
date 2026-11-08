"""Validate the two motivation-experiment paths in disposable transport processes."""
import argparse
import resource
from probe_glm_lifecycle_routes import Routes
from run_system import SystemRun, NODES, ROOT


class PressureRoutes(Routes):
    def __init__(self, args):
        NODES.clear()
        NODES.update({f'node{n}': {'ip': f'12.11.3.{n}', 'gpus': [0, 1],
                                 'container': f'zjn-keep-v02-n{n}'} for n in [27, 29]})
        SystemRun.__init__(self, args)
        self.env.update(AMD_SERIALIZE_KERNEL='0', NCCL_IB_HCA='shca_0', NCCL_SOCKET_IFNAME='ib0')
        self.result['scope'] = 'isolated 32MiB+17B bidirectional path validation on shared physical DCUs'


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.set_defaults(port_base=31300, worker_serialize=0, mesh='node27:0-1,node29:0-1',
                        result_root=str(ROOT/'codex_coding/results/transport_validation'), development='v0.1.23.2')
    PressureRoutes(parser.parse_args()).run()
