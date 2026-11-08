"""Test failure ownership journaling without transport, tensors or GPU operations."""
import argparse
import copy
import json
from pathlib import Path

import adapter
from cpu_api_check import fixture
from fluidckpt.cluster.catalog import Catalog


class MetadataOnlyCluster:
    executor = 'serial'

    def __init__(self, binding, contract):
        self.binding = binding
        self.endpoints = [w['endpoint'] for w in binding['workers']]
        self.catalogs = [Catalog(w['budget_bytes']) for w in binding['workers']]
        for c in adapter.projection(contract)['candidates']:
            if c['fixed']:
                desc = adapter.descriptor(c, binding, 'ignored', 'C0', True)
                catalog = self.catalogs[desc['worker']]
                catalog.reserve(desc, lambda: (None, None))
                catalog.publish(desc['id'], True)

    def inventory(self):
        ws = copy.deepcopy(self.binding['workers'])
        for w in ws:
            w['transactions'] = list(self.catalogs[w['worker']].transactions)
        return ws, [r for catalog in self.catalogs for r in catalog.inventory()]

    def transfer(self, plan, verify_before_ready=True):
        if plan['adapter_stage'] == 'restore':
            # A partially completed transaction may have published its first target.
            targets = plan['targets'][:1]
        else:
            targets = plan['targets']
        for target in targets:
            w = self.binding['workers'][target['worker']]
            desc = dict(target, epoch=w['epoch'], node=w['node'], gpu=w['gpu'])
            catalog = self.catalogs[w['worker']]
            catalog.reserve(desc, lambda: (None, None))
            catalog.publish(desc['id'], True)
        if plan['adapter_stage'] == 'restore':
            raise RuntimeError('injected restore failure after one target publication')
        return {'verification': {'passed': True}, 'scope': 'metadata mock; no transfer performed'}

    def retire(self, rid):
        for catalog in self.catalogs:
            if rid in catalog.entries:
                catalog.retire(rid)
                return
        raise ValueError('unexpected absent cleanup ID')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    contract = adapter.load_contract()
    binding = fixture(contract, ['http://127.0.0.1:1'] * 3)
    binding['purpose'] = 'physical'
    cluster = MetadataOnlyCluster(binding, contract)
    checks = []
    def check(name, value):
        checks.append(dict(name=name, passed=bool(value)))
        if not value:
            raise AssertionError(name)
    try:
        adapter.execute_phase(cluster, contract, binding, 'C0', 'journal_test')
    except ValueError as exc:
        check('changing fixture purpose cannot authorize execution', 'trusted admission gate' in str(exc))
    else:
        check('changing fixture purpose cannot authorize execution', False)
    def mock_gate(binding, phase, run_id):
        return dict(status='admitted', contract_hash=adapter.FROZEN, phase=phase, run_id=run_id,
                    scope='mock coordinator callback, CPU metadata-only test')
    try:
        adapter.execute_phase(cluster, contract, binding, 'C0', 'journal_test', trusted_admission=mock_gate,
                              journal_path=out / 'failed_run_journal.json')
    except adapter.AdapterExecutionError as exc:
        journal = exc.journal
        check('save success retained in failed journal', journal['save']['verification']['passed'])
        check('all eight cache and four proposed targets journaled before transfer', len(journal['owned_ids']) == 12)
        check('failure journal persisted', json.loads((out / 'failed_run_journal.json').read_text())['status'] == 'failed')
        cleanup = adapter.cleanup_owned(cluster, journal)
        check('cleanup handles missing unpublished targets', len(cleanup['retired']) == 9 and len(cleanup['absent_or_released']) == 3)
        check('cleanup checks all eight current base rows', len(cleanup['preserved_base_descriptors']) == 8 and cleanup['preservation_checked_from_current_catalog'])
        check('precise cleanup leaves base valid and leases zero', all(r['state'] == 'VALID' and r['leases'] == 0
              for r in cleanup['preserved_base_descriptors']))
        (out / 'cleanup.json').write_text(json.dumps(cleanup, indent=2) + '\n')
    else:
        check('injected failure caught', False)
    (out / 'qualification.json').write_text(json.dumps(dict(passed=True, checks=checks, check_count=len(checks),
        scope='CPU metadata mocks test failure ownership only; no native transfer or admission evidence',
        physical_ready=False, GPU_calls=0, remote_operations=0), indent=2) + '\n')
    print(json.dumps(dict(passed=True, checks=len(checks), output=str(out))))


if __name__ == '__main__':
    main()
