"""Pair actual restores under parallel-path and serial-target cost assumptions."""
import argparse
import resource
from pathlib import Path

from run_recall_pressure_motivation import PressureRun, request


class CostRun(PressureRun):
    def run(self):
        self.result['protocol'].update(role='paired causal probe of executor/cost-model agreement',
                                       policies=['parallel_path_model', 'serial_target_model'],
                                       sequence=[False, True, True, False])
        try:
            self.park('A'); self.park('B')
            self.prepare_ssd()
            _, replicas = self.cluster.inventory(compact=True)
            ids = set(self.models['B']['source_ids'])
            local = [r for r in replicas if r['id'] in ids and self.members[r['worker']][0] == 'node27']
            if sum(r['n'] for r in local) != self.result['runtime_bytes']:
                raise RuntimeError('the paired cost-model probe requires complete local DRAM coverage')
            self.allowed_ids = list(ids)+self.ssd_ids
            for repetition, serial in enumerate([False, True, True, False]):
                self.cluster.profile['serial_target_execution'] = serial
                policy = 'serial_target_model' if serial else 'parallel_path_model'
                before = request(self.ssd_endpoint, 'status')
                row = self.resume('B', f'{policy}_{repetition}')
                after = request(self.ssd_endpoint, 'status')
                ssd_bytes = sum(n for rid, n in self.last_source_bytes.items() if rid in self.ssd_ids)
                self.result['samples'].append({'policy': policy, 'fraction': 1., 'repetition': repetition,
                    'first_token_s': row['request_to_first_token_s'], 'correct': row['correct'],
                    'flow': row['flow'], 'selected_ssd_bytes': ssd_bytes,
                    'ssd_logical_read_bytes': after['read_bytes']-before['read_bytes'],
                    'ssd_kernel_read_bytes': after['kernel_io']['read_bytes']-before['kernel_io']['read_bytes']})
                self.park('B'); self.checkpoint_lifecycle()
                self.event('paired_cost_sample', policy=policy, first_token_s=row['request_to_first_token_s'],
                           selected_ssd_bytes=ssd_bytes)
            self.result['status'] = 'passed'
            self.event('paired_cost_complete')
        except BaseException as exc:
            self.result.update(status='failed', error=repr(exc)); self.event('failed_resources_retained', error=repr(exc))
            raise
        finally:
            self.checkpoint_lifecycle()


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--route-result', type=Path, required=True)
    parser.add_argument('--resume-run', type=Path, required=True)
    parser.set_defaults(fractions=[1.], repeats=1, wait_timeout=10800, cpu_first_ssd=False,
                        resume_failed=False, free_pages_only=True)
    CostRun(parser.parse_args()).run()
