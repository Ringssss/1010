"""Test the two future spill pairs in a separate mesh without touching model snapshots."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import resource

from run_system import SystemRun, ROOT, NODES
from fluidckpt.cluster.control import request


class Routes(SystemRun):
    def __init__(self, args):
        NODES.clear()
        NODES.update({'node27': {'ip': '12.11.3.27', 'gpus': [2, 3], 'container': 'zjn-keep-v02-n27'},
                      'node28': {'ip': '12.11.3.28', 'gpus': [6, 7], 'container': 'zjn-keep-v02-n28'}})
        super().__init__(args)
        self.env.update(AMD_SERIALIZE_KERNEL='0', NCCL_IB_HCA='shca_0', NCCL_SOCKET_IFNAME='ib0')
        self.result['scope'] = 'four temporary workers in separate containers and a separate mesh on shared physical DCUs; '
        self.result['scope'] += 'synthetic route correctness, not throughput under exclusive device ownership'

    def isolated_containers(self):
        for node in NODES:
            image = self.docker_host(node, ['inspect', NODES[node]['container'], '--format', '{{.Config.Image}}']).strip()
            name = 'zjn-route-only-'+self.args.run
            command = ['run', '-d', '--name', name, '--network', 'host', '--ipc', 'host',
                       '--cap-add', 'SYS_NICE', '--ulimit', 'memlock=-1:-1', '--ulimit', 'core=0:0',
                       '--memory', '32g', '--memory-swap', '32g']
            for dev in ['/dev/kfd', '/dev/mkfd', '/dev/dri', '/dev/infiniband']:
                command += ['--device', dev]
            command += ['-v', '/opt/hyhal:/opt/hyhal:ro', '-v', '/home/zhujianian:/home/zhujianian',
                        image, 'sleep', 'infinity']
            self.docker_host(node, command)
            self.owned_containers.append((node, name)); NODES[node]['container'] = name

    def preflight(self):
        self.probes = {}
        for node, info in NODES.items():
            path = self.out/f'{node}_topology.json'
            self.remote(node, ['python3', 'codex_coding/src/task2/cluster/inspect_node.py', '--out', str(path)])
            probe = json.loads(path.read_text()); self.probes[node] = probe
            for gpu in info['gpus']:
                memory = probe['gpu_memory'][f'card{gpu}']
                free_mib = int(memory['vram Total Memory (MiB)'])-int(memory['vram Total Used Memory (MiB)'])
                if free_mib < 2048:
                    raise MemoryError(f'{node}:{gpu} needs 2GiB free for the disposable route probe')
            if any(self.base+gpu in probe['occupied_tcp_ports'] for gpu in info['gpus']):
                raise RuntimeError('probe control port is occupied')
        self.event('disposable_mesh_preflight', nodes=list(NODES))

    def run(self):
        try:
            self.isolated_containers(); self.preflight(); self.start_workers()
            self.result['pairs'] = []
            for local, remote in [(0, 2), (1, 3)]:
                tag = 'fixture'+str(local)
                request(self.endpoints[local], 'synth', {'snap': tag, 'tp': 0, 'nbytes': (32 << 20)+17, 'seed': 53+local})
                source = request(self.endpoints[local], 'adopt', {'snap': tag, 'tp': 0, 'id': tag+'/source'})
                target = self.replica_target(source, tag+'/remote', remote, 'dram')
                outward = self.cluster.transfer(self.cluster.plan_restore([target], 'resource_lp', [source['id']]))
                back = self.replica_target(source, tag+'/back', local, 'hbm')
                inward = self.cluster.transfer(self.cluster.plan_restore([back], 'resource_lp', [target['id']]))
                self.result['pairs'].append({'source': self.members[local], 'target': self.members[remote],
                    'outward': outward['verification'], 'inward': inward['verification']})
                self.event('route_verified', source=self.members[local], target=self.members[remote])
            self.result['status'] = 'passed'
        except BaseException as exc:
            self.result.update(status='failed', error=repr(exc)); self.event('failed', error=repr(exc)); raise
        finally:
            for handle in list(reversed(self.processes)):
                try: self.stop(handle)
                except Exception as exc: self.result.setdefault('cleanup_errors', []).append(repr(exc))
            for node, name in self.owned_containers:
                try: self.docker_host(node, ['rm', '-f', name])
                except Exception as exc: self.result.setdefault('cleanup_errors', []).append(repr(exc))
            self.save()


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.set_defaults(port_base=31100, worker_serialize=0, mesh='node27:2-3,node28:6-7',
                        result_root=str(ROOT/'codex_coding/results/transport_validation'), development='v0.1.23.2')
    Routes(parser.parse_args()).run()
