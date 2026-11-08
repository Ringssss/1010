"""Independent native Cluster lifecycle fixture using observed pre-mesh rows."""
import argparse
import copy
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / 'codex_coding/src/v0.1.23.3/task2/bounded_cost/execution'))
import runner
from fluidckpt.cluster.control import Cluster


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    actual = ROOT / 'codex_coding/results/v0.1.23.3/task2/bounded_cost/execution_actual_r01'
    raw = json.loads((actual / 'startup.inventory.json').read_text())
    setup = json.loads((actual / 'runtime_setup.json').read_text())
    setup['valid_until'] = time.time() + 60
    checks = []

    def check(name, condition):
        checks.append(dict(name=name, passed=bool(condition)))
        assert condition, name

    class Fixture(Cluster):
        def __init__(self, workers):
            super().__init__(['fixture'] * 6, {})
            self.rows, self.events = copy.deepcopy(workers), []

        def call_all(self, operation, bodies=None, telemetry=None):
            self.events.append(operation)
            if operation == 'mesh':
                for index, row in enumerate(self.rows):
                    row['worker'] = index
                return [{'fixture_mesh': True}] * 6
            assert operation == 'inventory'
            return copy.deepcopy(self.rows)

    old = Fixture(raw)
    try:
        old.inventory()
    except ValueError as error:
        check('native_strict_inventory_reproduces_premesh_failure', str(error) == 'worker identities do not match endpoint order')
    else:
        check('native_strict_inventory_reproduces_premesh_failure', False)
    fixture = Fixture(raw)
    runner.initialize_mesh(fixture, setup, args.out)
    check('raw_before_mesh_strict_inventory_after_mesh', fixture.events == ['inventory', 'mesh', 'inventory'])
    check('actual_worker_rank_assigned_by_mesh', [w['worker'] for w in fixture.inventory()[0]] == list(range(6)))
    bad = copy.deepcopy(raw)
    bad[4]['runtime_identity']['actual_bdf'] = '0000:ff:00.0'
    wrong = Fixture(bad)
    (args.out / 'wrong_BDF').mkdir()
    try:
        runner.initialize_mesh(wrong, setup, args.out / 'wrong_BDF')
    except ValueError:
        check('physical_mismatch_refused_before_mesh', wrong.events == ['inventory'])
    else:
        check('physical_mismatch_refused_before_mesh', False)
    untouched = Fixture(raw)
    runner.raw_identity_and_empty(untouched.call_all('inventory'), setup)
    check('premesh_empty_cleanup_without_rank_assignment', untouched.events == ['inventory'] and all(w['worker'] is None for w in untouched.rows))
    source = Path(runner.__file__)
    qualification = dict(status='passed', checks=checks, check_count=len(checks),
                         CPU_fixture_only=True, physical_runtime_certified=False, GPU_or_SSH=False,
                         runner_ref=runner.ref(source), historical_startup_ref=runner.ref(actual / 'startup.inventory.json'))
    (args.out / 'qualification.json').write_text(json.dumps(qualification, indent=2) + '\n')
    print(json.dumps(dict(status='passed', checks=len(checks))))


if __name__ == '__main__':
    main()
