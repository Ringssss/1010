"""Register frozen layouts on CPU; explicitly execute one protected TP2 restore."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import resource
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / 'main_lib'))
sys.path.insert(0, str(ROOT / 'codex_coding/src/v0.1.23.3/task2/basic_scenarios'))
sys.path.insert(0, str(ROOT / 'codex_coding/src/v0.1.23.3/task2/baseline_loading/blitz'))
from blitz_freeze import project_imports
import runtime_support
from runtime_support import digest, http, load_state, select_base, source_signature, target, write
from run_scenarios import ScenarioRun
from run_layout_scenarios import LayoutScenarioRun, OrderCluster, reordered_plan, verify_runtime_order
from fluidckpt.benchmarks.frozen_layout import (bind_real_cache, compare_placement, layout_ledger,
                                               read_frozen)

RESULT_ROOT = ROOT / 'codex_coding/results/v0.1.23.3/task2/keeper_bridge'
DEFAULT_SESSION = ROOT / 'codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json'
DEFAULT_MANIFEST = ROOT / 'codex_coding/results/v0.1.23.3/task2/topology_scenarios/scenarios_freeze_probe_r08/manifest.json'
DEFAULT_PROTECT = ROOT / 'codex_coding/results/motivation/fig4_lanes11/session.json'


def dependency_freeze():
    seeds = [Path(__file__),
             ROOT / 'codex_coding/src/v0.1.23.3/task2/basic_scenarios/run_scenarios.py',
             ROOT / 'codex_coding/src/v0.1.23.3/task2/basic_scenarios/run_layout_scenarios.py',
             ROOT / 'codex_coding/src/v0.1.23.3/task2/basic_scenarios/runtime_support.py',
             ROOT / 'codex_coding/src/v0.1.23.3/task2/baseline_loading/blitz/blitz_freeze.py',
             ROOT / 'codex_coding/src/serving/sglang_fluid_launch.py',
             ROOT / 'main_lib/fluidckpt/cluster/worker.py']
    seen = set()
    while seeds:
        path = seeds.pop().resolve()
        if path in seen:
            continue
        seen.add(path)
        seeds.extend(project_imports(path, ROOT))
    return {str(path.relative_to(ROOT)): digest(path) for path in sorted(seen)}


def new_out(path):
    path = Path(path).resolve()
    if RESULT_ROOT not in path.parents or not path.name.startswith('layout_'):
        raise ValueError('use a new keeper_bridge/layout_* result directory')
    path.mkdir(parents=True, exist_ok=False)
    return path


def selection(case, profile, name):
    fixed = next((r for r in case['fixed_layouts'] if r['name'] == name), None)
    if fixed:
        by_id = {c['id']: c for c in case['candidates']}
        return [copy.deepcopy(by_id[rid]) for rid in fixed['candidate_ids']], None
    if name not in ('strong_simple', 'joint'):
        raise ValueError('layout must name a frozen fixed layout, strong_simple or joint')
    comparison = compare_placement(case, profile)
    return comparison['policies'][name]['selected'], comparison


def protected_inventory(session_path):
    state = load_state(session_path)
    def read(ep):
        try:
            row = http(ep, 'inventory?compact=1', timeout=5)
            return dict(endpoint=ep, status='reachable', worker=row['worker'], epoch=row['epoch'],
                        replicas=source_signature(row['replicas']), transactions=row['transactions'],
                        used_bytes=row['used_bytes'], staging_bytes=row['staging_bytes'],
                        hbm_allocated_bytes=row['hbm_allocated_bytes'],
                        hbm_reserved_bytes=row['hbm_reserved_bytes'])
        except Exception:
            return dict(endpoint=ep, status='unreachable')
    with ThreadPoolExecutor(len(state['endpoints'])) as pool:
        return list(pool.map(read, state['endpoints']))


class FrozenLayoutRun(LayoutScenarioRun):
    """Override frozen inputs/build only; keep established restore and cleanup gates."""
    def __init__(self, args, frozen, profile, registered, selected):
        self.frozen, self.registered, self.selected = frozen, registered, copy.deepcopy(selected)
        self.schedule = 'rank_interleaved'
        self.canonical_ops = None
        self.config = {'schedules': ['rank_interleaved']}
        # The existing initializer requires S4 eligibility and its known methods.
        # Redirect only this process's output helper, leaving old source immutable.
        runtime_support.RESULT_ROOT = RESULT_ROOT
        ScenarioRun.__init__(self, args)
        self.cluster = OrderCluster(self.cluster, self)
        self.freeze = dependency_freeze()
        self.result['code_sha256'] = self.freeze
        self.result['code_freeze_scope'] = 'local import closure plus retained worker/engine entry points'
        if self.cluster.profile != profile:
            raise ValueError('runtime profile differs from frozen CPU contract')
        self.cache_slot = copy.deepcopy(self.state['slots']['target_a'])
        self.layout = copy.deepcopy(selected)
        self.ledger = layout_ledger(frozen, selected)
        self.cache_needed = {int(k.split(':')[0]): n for k, n in self.ledger['location_bytes'].items() if n}
        chosen = next(s for s in frozen['restore_scenarios'] if s['name'] == args.target_slot)
        if [t['worker'] for t in sorted(chosen['targets'], key=lambda t: t['tp'])] != self.target_slot['workers']:
            raise ValueError('target slot no longer matches mutually exclusive frozen target')
        if any(s['identity'] != b['identity'] or s['n'] != b['n']
               for s, b in zip(self.model['sources'], sorted(frozen['base'], key=lambda b: b['tp']))):
            raise ValueError('retained processed snapshot differs from frozen identities/extents')
        paths = [Path(__file__), ROOT / 'main_lib/fluidckpt/benchmarks/frozen_layout.py', args.manifest,
                 args.manifest.parent / 'cases' / (args.case + '.json'), args.session, args.protect_session]
        self.input_freeze = {str(p.resolve()): digest(p) for p in paths}
        self.freeze[str(Path(__file__).relative_to(ROOT))] = digest(__file__)
        self.freeze['main_lib/fluidckpt/benchmarks/frozen_layout.py'] = digest(paths[1])
        self.result.update(scene_id=args.case, requested_frozen_case=args.case,
                           selected_layout=args.layout, selected_candidates=self.selected,
                           frozen_manifest=str(args.manifest.resolve()), frozen_manifest_sha256=digest(args.manifest),
                           frozen_case_sha256=registered['case_sha256'], mutually_exclusive_target=args.target_slot,
                           planned_cache_layout=self.layout, builds=[], budget=frozen['budget'],
                           setup_ledger=self.ledger, profile_source='functional_provisional_assumption',
                           prediction_ready=False, physical_contention_claim_ready=False,
                           gate_kind='frozen_layout_functional', experiment_id='e3',
                           common_schedule='rank_interleaved', input_sha256=self.input_freeze)
        self.result['protocol'].update(source_availability='complete retained DRAM plus built and verified frozen cache copies',
                                       scene=frozen['case_class'], schedules=['rank_interleaved'],
                                       budget_origin=frozen['budget']['budget_origin'],
                                       mutually_exclusive_target=args.target_slot,
                                       verification_position='after_weights_ready')
        self.save()

    def preflight(self):
        read_frozen(self.args.manifest, self.args.case, ROOT, self.args.session)
        self.protect_before = protected_inventory(self.args.protect_session)
        write(self.out / 'protected_legacy_before.json', self.protect_before)
        if sum(r['status'] == 'reachable' for r in self.protect_before) != 14:
            raise ValueError('protected legacy reachability differs from retained 14-worker state')
        ScenarioRun.preflight(self)
        wm = {w['worker']: w for w in self.before}
        for old in self.frozen['workers']:
            if any(wm[old['worker']][k] != old[k] for k in ('node', 'gpu', 'numa')):
                raise ValueError('runtime physical worker location differs from frozen location')
        if self.cache_slot['endpoint'] != self.target_slot['endpoint']:
            self.result['parked_preflight'][self.cache_slot['endpoint']] = self.parked_evidence(self.cache_slot)
        selected_target_workers = {t['worker']: t['n'] for t in next(
            s for s in self.frozen['restore_scenarios'] if s['name'] == self.args.target_slot)['targets']}
        for wid, w in wm.items():
            n = self.ledger['location_bytes'][f'{wid}:hbm']
            if n > w['budget_bytes']['hbm'] - w['used_bytes']['hbm']:
                raise MemoryError('cache catalog admission failed')
            required = n + selected_target_workers.get(wid, 0) + (self.args.guard_mib << 20)
            if required > w['headroom_bytes']['hbm']:
                raise MemoryError('cache plus selected target physical HBM admission failed')
        self.result['cache_preflight'] = self.ledger
        self.save()

    def case(self, workers, replicas, targets):
        base = select_base(self.model, replicas)
        actual = [r for r in replicas if r['id'] in set(self.owned)]
        cache = bind_real_cache(self.frozen, self.selected, actual, workers, self.owned)
        return dict(scenario_id='S4', workers=workers,
                    replicas=[dict(r, source_role='base', allocation_id=r['id']) for r in base] +
                             [dict(r, source_role='extra', allocation_id=r['id']) for r in cache],
                    targets=targets, profile=self.cluster.profile, executor='serial',
                    format_contract=self.result['format_contract'],
                    budget=dict(base_dram_bytes=self.frozen['budget']['base_dram_bytes'],
                                extra_hbm_bytes=self.frozen['budget']['tier_budgets']['hbm'],
                                location_extra_hbm_bytes=self.frozen['budget']['location_budgets'],
                                target_allocation_bytes=self.frozen['budget']['target_allocation_bytes'],
                                staging_bytes=sum(w['staging_bytes'] for w in workers)))

    def build_cache(self):
        started = time.monotonic()
        build = dict(status='running', flows=[], outside_restore_window=True,
                     selected_candidates=self.selected, ledger=self.ledger)
        self.result['builds'].append(build)
        for i, row in enumerate(self.selected):
            rid = f'{self.prefix}/cache/{i}'
            self.owned.append(rid)
            self.result['owned_replica_ids'] = list(self.owned)
            self.save()
            t = target(self.model['sources'][row['tp']], rid, row['worker'], off=row['off'], n=row['n'])
            flow = self.cluster.transfer(self.cluster.plan_restore([t], 'resource_lp', self.model['source_ids']))
            build['flows'].append(flow)
            if (flow['plan']['strategy'] != 'resource_lp' or not flow['verification']['passed']
                    or flow['verification']['checked_bytes'] != row['n']):
                raise AssertionError('each actual cache copy must pass full logical-byte SHA')
            self.save()
        workers, replicas = self.memory('cache_ready')
        actual = [r for r in replicas if r['id'] in set(self.owned)]
        bound = bind_real_cache(self.frozen, self.selected, actual, workers, self.owned)
        build.update(status='passed', wall_s=time.monotonic() - started, actual_replicas=bound,
                     candidate_runtime_bindings=[dict(candidate_id=c['id'], actual_id=r['id'],
                         epoch=r['epoch'], generation=r['generation'], allocated_bytes=r['allocated_bytes'])
                         for c, r in zip(self.selected, bound)])
        self.result['setup_ledger'].update(cache_build_s=build['wall_s'], cache_build_bytes=self.ledger['cache_build_logical_bytes'],
            owner_setup_s=0, format_setup_s=0, pool_setup_s=0,
            setup_scope='reuse retained owner/processed format/pool; cache build transfer includes prepare/verify',
            staging_peak_bytes=max(sum(w['staging_bytes'] for w in s['workers']) for s in self.result['memory_samples']),
            physical_peak_bytes=None, physical_peak_scope='point samples only; continuous physical peak unverified')
        self.save()

    def scheduled(self, plan):
        if any(o['replica_id'].startswith('preview/') for o in plan['ops']):
            raise ValueError('hypothetical source ID reached the runtime planner')
        return reordered_plan(plan, 'rank_interleaved')

    def restore(self, method, repetition):
        ScenarioRun.restore(self, method, repetition)
        row = self.result['cases'][-1]
        row.update(frozen_case=self.args.case, layout=self.args.layout,
                   target_scenario=self.args.target_slot, schedule='rank_interleaved',
                   physical_resource_gate='conditional', prediction_ready=False)
        row['runtime_order_verification'] = verify_runtime_order(row['flow']['plan'], row['flow']['execution'])
        self.result['setup_ledger'].update(online_planning_s=row['online_planning_s'],
                                          prepare_s=row['flow']['prepare_s'])
        self.save()

    def cleanup(self):
        LayoutScenarioRun.cleanup(self)
        try:
            if hasattr(self, 'protect_before'):
                after = protected_inventory(self.args.protect_session)
                write(self.out / 'protected_legacy_after.json', after)
                self.result['legacy_keepers_unchanged'] = after == self.protect_before
                if after != self.protect_before:
                    raise AssertionError('protected legacy keeper inventory changed')
            mismatches = [p for p, h in self.input_freeze.items() if digest(p) != h]
            if mismatches:
                raise AssertionError('frozen driver/input changed: ' + repr(mismatches))
            read_frozen(self.args.manifest, self.args.case, ROOT, self.args.session)
            self.result['frozen_input_code_preserved'] = True
        except Exception as exc:
            self.result['cleanup_errors'].append(repr(exc))
        self.save()


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument('--session', type=Path, default=DEFAULT_SESSION)
    parser.add_argument('--protect-session', type=Path, default=DEFAULT_PROTECT)
    parser.add_argument('--case', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--compare-placement', action='store_true')
    parser.add_argument('--layout')
    parser.add_argument('--target-slot')
    parser.add_argument('--method', choices=('janus_host', 'fastest', 'resource_lp'), default='fastest')
    parser.add_argument('--guard-mib', type=int, default=2048)
    parser.add_argument('--allocation-tolerance-mib', type=int, default=32)
    parser.add_argument('--physical-tolerance-mib', type=int, default=256)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if any(getattr(args, k) < 0 for k in ('guard_mib', 'allocation_tolerance_mib', 'physical_tolerance_mib')):
        parser.error('negative admission/cleanup tolerance')
    case, profile, registered = read_frozen(args.manifest, args.case, ROOT, args.session)
    if args.compare_placement:
        if args.execute or args.layout or args.target_slot:
            parser.error('placement comparison is separate CPU-only registration')
        out = new_out(args.out)
        result = dict(status='running', new_GPU_samples=0, measurement_status='not_measured')
        write(out / 'summary.json', result)
        try:
            result = compare_placement(case, profile)
            result.update(manifest_sha256=digest(args.manifest), session_sha256=digest(args.session),
                          code_sha256=dependency_freeze(), command=[sys.executable, *sys.argv],
                          frozen_inputs_verified=True)
            write(out / 'summary.json', result)
        except Exception as exc:
            result.update(status='failed', error=repr(exc), traceback=traceback.format_exc())
            write(out / 'summary.json', result)
            raise
        print(json.dumps(dict(status=result['status'], decisions_equal=result['decisions_equal'], out=str(out))))
        return
    if not args.layout or not args.target_slot:
        parser.error('one --layout and one mutually exclusive --target-slot required')
    if args.target_slot not in {s['name'] for s in case['restore_scenarios']}:
        parser.error('target is not part of this frozen case')
    selected, comparison = selection(case, profile, args.layout)
    args.scene, args.methods, args.source_slot, args.repetitions = 'S4', [args.method], 'target_a', 1
    if RESULT_ROOT not in args.out.resolve().parents or not args.out.name.startswith('layout_'):
        parser.error('use a new keeper_bridge/layout_* result directory')
    runner = FrozenLayoutRun(args, case, profile, registered, selected)
    if comparison:
        write(runner.out / 'placement_comparison.json', comparison)
    if args.execute:
        if not runner.run():
            raise SystemExit(1)
    else:
        print(json.dumps(dict(status='registered_only', new_GPU_samples=0, out=str(runner.out))))


if __name__ == '__main__':
    main()
