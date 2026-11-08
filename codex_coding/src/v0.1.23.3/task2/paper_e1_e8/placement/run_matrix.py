"""Measured Qwen placement cases on explicit, already booted and parked slots."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / 'main_lib'))
from fluidckpt.cluster.control import Cluster, request
from fluidckpt.cluster.planning import covers
from fluidckpt.serving.handoff import wait_for_exports
from fluidckpt.serving.measure import summarize

GIB = 1 << 30


def layout_signature(selected):
    return sorted((r['identity'], r['tp'], r['worker'], r['tier'], r['off'], r['n']) for r in selected)


def route_signature(plan):
    # Preserve executor order; operation numbering is bookkeeping, source offsets are not.
    fields = ('src_w', 'dst_w', 'identity', 'tp', 'off', 'source_offset', 'replica_id', 'n', 'kind', 'path')
    return [tuple(op.get(k) for k in fields) for op in plan['ops']]


class MatrixRun:
    def __init__(self, args):
        urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
        self.args = args
        self.state = json.loads(args.session.read_text())
        self.model = self.state['models']['B']
        self.slots = {name: self.state['slots'][name] for name in ('target_a', 'target_b')}
        if self.model['tp'] != 2 or len(self.slots) != 2:
            raise ValueError('this protocol requires TP2 and two mutually exclusive slots')
        if len({s['endpoint'] for s in self.slots.values()}) != 2:
            raise ValueError('the target slots must refer to distinct engines')
        if not all(s['parked'] for s in self.slots.values()):
            raise ValueError('all slots must be parked before entry')
        self.out = args.run_dir.resolve()
        self.out.mkdir(parents=True, exist_ok=False)
        self.prefix = 'paper_'+self.out.name
        self.handoff = Path(self.state['handoff'])
        self.cluster = Cluster(self.state['endpoints'], self.state['profile'], executor='serial')
        self.owned = []
        self.files = sorted((ROOT/'main_lib/fluidckpt').rglob('*.py')) + sorted(Path(__file__).parent.glob('*.py'))
        self.hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.files}
        self.result = {'status': 'running', 'stage': args.stage, 'protocol': vars(args).copy(),
                       'session_sha256': hashlib.sha256(args.session.read_bytes()).hexdigest(),
                       'code_sha256': self.hashes, 'cases': [], 'builds': [], 'cleanup_errors': [],
                       'measurement': 'same-host max(weights_ready)-min(load_begin); verification after ready',
                       'budget_scope': 'fixed full DRAM base plus charged extra HBM; unrelated owners recorded separately',
                       'comparison_scope': 'frozen baseline/candidate routing; strongest baseline only if justified by prior measured e4',
                       'physical_high_water_verified': False}
        self.result['protocol'] = {k: str(v) if isinstance(v, Path) else v for k, v in self.result['protocol'].items()}
        self.save()

    def save(self):
        temp = self.out/'summary.json.tmp'
        temp.write_text(json.dumps(self.result, indent=2, allow_nan=False)+'\n')
        temp.replace(self.out/'summary.json')

    def event(self, name, **fields):
        with (self.out/'events.jsonl').open('a') as stream:
            stream.write(json.dumps(dict(event=name, wall_time=time.time(), **fields))+'\n')

    def inventory(self):
        workers, replicas = self.cluster.inventory(compact=True)
        if any(w['transactions'] for w in workers):
            raise RuntimeError('another transaction is active')
        by_id = {r['id']: r for r in replicas}
        base = [by_id[rid] for rid in self.model['source_ids']]
        if any(r['tier'] != 'dram' for r in base):
            raise ValueError('this budget curve fixes a complete DRAM base')
        for s in self.model['sources']:
            if not covers(base, s['identity'], s['tp'], 0, s['n']):
                raise ValueError('base coverage incomplete')
        return workers, base

    def target(self, source, rid, worker, off=0, n=None, **extra):
        target = dict(key=rid, id=rid, mode='replica', worker=worker, tier='hbm',
                      identity=source['identity'], tp=source['tp'], off=off,
                      n=source['n'] if n is None else n, meta=source['meta'], index=source['index'])
        target.update(extra)
        return target

    def compact_flow(self, flow):
        # Full tensor layouts remain in the hashed overlay; preserve routes and timing here.
        plan = dict(flow['plan'])
        plan['targets'] = [{k: v for k, v in t.items() if k not in ('meta', 'index')}
                           for t in plan['targets']]
        return dict(flow, plan=plan)

    def scenarios(self):
        return [{'name': name, 'weight': 1, 'targets': [
            {k: v for k, v in self.target(s, f'future/{name}/{r}', slot['workers'][r]).items()
             if k not in ('meta', 'index')}
            for r, s in enumerate(self.model['sources'])]} for name, slot in self.slots.items()]

    def manual(self, name, budget, workers):
        slots = list(self.slots.values())
        if budget % (4*4096):
            raise ValueError('manual budget must divide into page-aligned quarters')
        assignments = ([(0, slots[0]['workers'][0], budget//2), (1, slots[0]['workers'][0], budget//2)]
                       if name == 'concentrated' else
                       [(0, slots[0]['workers'][0], budget//2), (1, slots[0]['workers'][1], budget//2)])
        if name == 'replicated':
            assignments = [(r, slot['workers'][r], budget//4) for slot in slots for r in range(2)]
        if name == 'hybrid':
            assignments = [(0, slots[0]['workers'][0], budget//2),
                           (1, slots[0]['workers'][1], budget//4), (1, slots[1]['workers'][1], budget//4)]
        wm, rows = {w['worker']: w for w in workers}, []
        for i, (rank, worker, size) in enumerate(assignments):
            if not size:
                continue
            source = self.model['sources'][rank]
            if size > source['n']:
                raise ValueError('cache exceeds rank')
            rows.append(dict(id=f'{name}/{i}', identity=source['identity'], tp=rank, off=0, n=size,
                             tier='hbm', worker=worker, node=wm[worker]['node'], numa=wm[worker]['numa'],
                             generation=1, state='VALID'))
        return rows

    def build(self, selected, label, budget):
        workers, base = self.inventory()
        wm = {w['worker']: w for w in workers}
        needed = {}
        for c in selected:
            needed[c['worker']] = needed.get(c['worker'], 0)+(c['n']+4095)//4096*4096
        if sum(needed.values()) > budget:
            raise ValueError('HBM budget violation')
        for w, n in needed.items():
            if n > wm[w]['budget_bytes']['hbm']-wm[w]['used_bytes']['hbm']:
                raise MemoryError(f'worker {w} catalog headroom insufficient')
            output = max((self.model['sources'][rank]['n'] for slot in self.slots.values()
                          for rank, dst in enumerate(slot['workers']) if dst == w), default=0)
            if n+output+(2 << 30) > wm[w]['headroom_bytes']['hbm']:
                raise MemoryError(f'worker {w} physical HBM headroom insufficient')
        # Every policy gets identical coalescing, independent of its candidate count.
        groups = []
        for c in sorted(selected, key=lambda r: (r['tp'], r['worker'], r['off'])):
            if groups and all(groups[-1][k] == c[k] for k in ('tp', 'worker', 'tier')) and groups[-1]['off']+groups[-1]['n'] == c['off']:
                groups[-1]['n'] += c['n']
            else:
                groups.append(dict(c))
        stamp = time.monotonic()
        build = {'label': label, 'budget_bytes': budget, 'selected': selected, 'flows': [],
                 'base_logical_bytes': sum(r['n'] for r in base),
                 'base_page_rounded_bytes': sum((r['n']+4095)//4096*4096 for r in base),
                 'cache_page_rounded_bytes': sum(needed.values()), 'before': workers}
        self.result['builds'].append(build)
        self.save()
        for i, c in enumerate(groups):
            rid = f'{self.prefix}/{label}/{i}'
            t = self.target(self.model['sources'][c['tp']], rid, c['worker'], c['off'], c['n'])
            self.owned.append((c['worker'], rid))
            plan = self.cluster.plan_restore([t], 'resource_lp', self.model['source_ids'])
            flow = self.cluster.transfer(plan)
            build['flows'].append(self.compact_flow(flow))
            if not flow['verification']['passed']:
                raise AssertionError('cache build verification failed')
            self.save()
        build.update(build_wall_s=time.monotonic()-stamp, after=self.inventory()[0])
        self.save()
        return list(self.model['source_ids'])+[rid for _, rid in self.owned]

    def clear(self):
        for worker, rid in list(reversed(self.owned)):
            self.cluster.retire(rid)
            self.owned.remove((worker, rid))

    def prewarm(self):
        source = self.model['sources'][0]
        size = min(4 << 20, source['n'])
        destinations = sorted({w for slot in self.slots.values() for w in slot['workers']})
        workers, _ = self.inventory()
        for w in workers:
            if w['worker'] in destinations and (w['budget_bytes']['hbm']-w['used_bytes']['hbm'] < 2*size
                    or w['headroom_bytes']['hbm'] < 2*size+(2 << 30)):
                raise MemoryError('insufficient headroom for disposable path warmup')
        stamp = time.monotonic()
        report = {'bytes_per_copy': size, 'workers': destinations, 'source_builds': [], 'copies': [],
                  'max_logical_hbm_bytes': (len(destinations)+1)*size,
                  'scope': 'one disposable HBM source per candidate worker, all source/destination pairs; no rate calibration'}
        self.result['prewarm'] = report
        self.save()
        warm_sources = {}
        for w in destinations:
            rid = f'{self.prefix}/warm/source/{w}'
            target = self.target(source, rid, w, n=size)
            self.owned.append((w, rid))
            flow = self.cluster.transfer(self.cluster.plan_restore([target], 'resource_lp', self.model['source_ids']))
            if not flow['verification']['passed']:
                raise AssertionError('warm source verification failed')
            warm_sources[w] = rid
            report['source_builds'].append(self.compact_flow(flow))
        for src in destinations:
            for dst in destinations:
                rid = f'{self.prefix}/warm/copy/{src}/{dst}'
                target = self.target(source, rid, dst, n=size)
                self.owned.append((dst, rid))
                flow = self.cluster.transfer(self.cluster.plan_restore([target], 'fastest', [warm_sources[src]]))
                if not flow['verification']['passed']:
                    raise AssertionError('warm path verification failed')
                report['copies'].append(self.compact_flow(flow))
                self.cluster.retire(rid)
                self.owned.remove((dst, rid))
        self.clear()
        report.update(wall_s=time.monotonic()-stamp, status='passed', final_inventory=self.inventory()[0])
        self.save()

    def generate(self, slot):
        payload = {'text': self.state.get('prompt', 'The capital of France is'),
                   'sampling_params': {'temperature': 0, 'max_new_tokens': 32}, 'stream': True}
        req = urllib.request.Request(slot['endpoint']+'/generate', data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
        first, last = None, {}
        with urllib.request.urlopen(req, timeout=180) as response:
            for raw in response:
                if not raw.startswith(b'data: '):
                    continue
                data = raw[6:].strip()
                if data == b'[DONE]':
                    break
                last = json.loads(data)
                if first is None and (last.get('text') or last.get('meta_info', {}).get('completion_tokens', 0)):
                    first = time.monotonic()
        if first is None:
            raise RuntimeError('no generated token')
        return {'text': last['text'], 'first_token_time': first}

    def restore(self, label, scenario, routing, source_ids):
        slot = self.slots[scenario]
        if not slot['parked']:
            raise ValueError('slot must be parked before restore')
        phase = f'{self.prefix}_{label}_{scenario}_{routing}'
        metrics = self.out/'metrics'/phase
        if metrics.exists() or (self.handoff/phase).exists():
            raise FileExistsError(phase)
        command = Path(slot['command'])
        command.write_text(json.dumps({'tag': phase, 'mode': 'restore', 'metrics': str(metrics)}))
        targets = [dict(self.target(s, f'{phase}/{r}', slot['workers'][r]), mode='live', tag=phase,
                        metrics_directory=str(metrics)) for r, s in enumerate(self.model['sources'])]
        # Both routing variants plan inside the same request-to-ready window.
        flow = None
        row = {'label': label, 'scenario': scenario, 'routing': routing, 'phase': phase, 'status': 'running'}
        self.result['cases'].append(row)
        slot['parked'] = None
        self.save()
        stamp = time.monotonic()
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(request, slot['endpoint'], 'resume_memory_occupation', {'tags': ['weights']}, 1200)
            wait = wait_for_exports(self.handoff/phase, ranks=2, timeout=1200)
            plan = self.cluster.plan_restore(targets, routing, source_ids)
            if plan['strategy'] != routing:
                raise RuntimeError('routing strategy fell back')
            flow = self.cluster.transfer(plan, verify_before_ready=False, retain=True)
            future.result()
        slot['parked'] = False
        row.update(request_to_ready_ack_s=time.monotonic()-stamp, handoff_wait=wait, flow=self.compact_flow(flow),
                   timing=summarize(metrics, 2))
        try:
            reply = self.generate(slot)
            row.update(reply=reply, request_to_first_token_s=reply['first_token_time']-stamp,
                       correct=reply['text'] == self.model['reference']['text'])
            row['verification'] = self.cluster.verify(flow['tx'])
            if not row['correct'] or not row['verification']['passed']:
                raise AssertionError('restored model correctness failed')
            row['status'] = 'passed'
        finally:
            self.cluster.finish(flow['tx'])
            request(slot['endpoint'], 'release_memory_occupation', {'tags': ['weights']}, timeout=300)
            slot['parked'] = True
            self.save()
        print(json.dumps({'phase': phase, 'T_ready_s': row['timing']['load_s'], 'correct': row['correct']}), flush=True)

    def solve(self, budget):
        workers, base = self.inventory()
        candidates = []
        block = self.args.block_gib*GIB
        # Equal candidate windows and per-card limits for both policies.
        for gi, slot in enumerate(self.slots.values()):
            for rank, source in enumerate(self.model['sources']):
                worker = slot['workers'][rank]
                w = next(w for w in workers if w['worker'] == worker)
                for bi, off in enumerate(range(0, min(source['n'], max(budget, block)), block)):
                    n = min(block, source['n']-off)
                    candidates.append(dict(id=f'cand/{gi}/{rank}/{bi}', identity=source['identity'], tp=rank,
                        worker=worker, node=w['node'], numa=w['numa'], tier='hbm', off=off, n=n,
                        generation=1, state='VALID', placement_group=gi, block=bi))
        locations = {}
        for c in candidates:
            w = next(w for w in workers if w['worker'] == c['worker'])
            output = max(self.model['sources'][rank]['n'] for slot in self.slots.values()
                         for rank, dst in enumerate(slot['workers']) if dst == c['worker'])
            locations[f"{c['worker']}:hbm"] = max(0, min(budget,
                w['budget_bytes']['hbm']-w['used_bytes']['hbm'], w['headroom_bytes']['hbm']-output-(2 << 30)))
        case = {'base': base, 'candidates': candidates, 'workers': workers, 'scenarios': self.scenarios(),
                'profile': self.cluster.profile, 'tier_budgets': {'hbm': budget}, 'location_budgets': locations}
        src, dst = self.out/f'budget_{budget}_case.json', self.out/f'budget_{budget}_solutions.json'
        src.write_text(json.dumps(case, indent=2)+'\n')
        subprocess.run([sys.executable, str(Path(__file__).with_name('solve_matrix.py')),
                        '--input', str(src), '--output', str(dst)], check=True)
        return json.loads(dst.read_text())

    def comparison_evidence(self, budget, solution):
        previous = self.args.comparison_run
        if previous is None:
            return None
        previous = previous.resolve()
        summary = json.loads((previous/'summary.json').read_text())
        if summary['stage'] != 'e4' or summary['status'] != 'passed':
            raise ValueError('comparison run must be a passed real-model e4')
        old_case = json.loads((previous/f'budget_{budget}_case.json').read_text())
        case = json.loads((self.out/f'budget_{budget}_case.json').read_text())
        old_solution = json.loads((previous/f'budget_{budget}_solutions.json').read_text())
        if old_case['profile'] != case['profile']:
            raise ValueError('comparison profile differs')
        fields = ('id', 'identity', 'tp', 'off', 'n', 'worker', 'tier', 'numa', 'epoch', 'generation')
        base_signature = lambda c: sorted(tuple(r.get(k) for k in fields) for r in c['base'])
        if base_signature(old_case) != base_signature(case):
            raise ValueError('comparison base coverage or generation differs')
        physical = lambda c: [(w['worker'], w['node'], w['gpu'], w['numa']) for w in c['workers']]
        if physical(old_case) != physical(case):
            raise ValueError('comparison topology differs')
        for path, sha in self.hashes.items():
            if path.startswith('main_lib/') and summary['code_sha256'].get(path) != sha:
                raise ValueError('comparison measured product code differs: '+path)
        evidence = []
        for policy, route in [('greedy', self.args.baseline_routing), ('joint', self.args.candidate_routing)]:
            current = solution['policies'][policy]
            for scenario in self.slots:
                matched = None
                for row in summary['cases']:
                    if row['scenario'] != scenario or row['status'] != 'passed':
                        continue
                    old_policy = next((p for p in ('greedy', 'joint') if row['label'] == f'{budget//GIB}gib_{p}'), None)
                    if old_policy is None:
                        continue
                    old = old_solution['policies'][old_policy]
                    if (layout_signature(current['selected']) == layout_signature(old['selected'])
                            and route_signature(current['routing'][route]['scenario_plans'][scenario]) ==
                            route_signature(old['routing'][row['routing']]['scenario_plans'][scenario])):
                        if not row['correct'] or not row['verification']['passed'] or row['timing']['load_s'] <= 0:
                            raise ValueError('comparison correctness or timing missing')
                        matched = {'policy': policy, 'routing': route, 'scenario': scenario,
                                   'source_phase': row['phase'], 'T_ready_s': row['timing']['load_s'],
                                   'measurement_kind': 'reference to existing e4 sample, not a new observation'}
                        break
                if matched is None:
                    raise ValueError('comparison lacks a measured matching layout and ordered route')
                evidence.append(matched)
        return {'run': str(previous), 'summary_sha256': hashlib.sha256((previous/'summary.json').read_bytes()).hexdigest(),
                'samples': evidence}

    def run(self):
        self.event('run_begin', stage=self.args.stage)
        try:
            self.result['initial_inventory'] = self.inventory()[0]
            self.save()
            if self.args.stage == 'e3':
                self.prewarm()
                budget = self.args.budget_gib*GIB
                names = ['concentrated', 'distributed'] + (['replicated', 'hybrid'] if self.args.extra_layouts else [])
                for i, name in enumerate(names):
                    selected = self.manual(name, budget, self.inventory()[0])
                    sources = self.build(selected, name, budget)
                    order = list(self.slots)[::1 if i % 2 == 0 else -1]
                    for scenario in order:
                        self.restore(name, scenario, 'resource_lp', sources)
                    self.clear()
            else:
                budgets = ([self.args.budget_gib] if self.args.stage in ('e4', 'e5') else
                           [int(v) for v in self.args.budgets_gib.split(',')])
                for gib in budgets:
                    budget = gib*GIB
                    solution = self.solve(budget)
                    baseline = solution['policies']['greedy']
                    candidate = solution['policies']['joint']
                    ordered_routes_equal = all(
                        route_signature(baseline['routing'][self.args.baseline_routing]['scenario_plans'][s]) ==
                        route_signature(candidate['routing'][self.args.candidate_routing]['scenario_plans'][s])
                        for s in self.slots)
                    if self.args.stage == 'e5' and solution['layout_signatures_equal'] and ordered_routes_equal:
                        comparison = self.comparison_evidence(budget, solution)
                        if comparison is not None:
                            self.result.update(status='decision_only', decision='unchanged', comparison=comparison,
                                reason='same placement and ordered executable routes; existing e4 correctness and timings referenced',
                                new_gpu_restore_count=0, speedup_claim=None)
                            self.save()
                            continue
                    if 'prewarm' not in self.result:
                        self.prewarm()
                    policies = ['greedy', 'joint']
                    same_routing = self.args.baseline_routing == self.args.candidate_routing
                    if solution['layout_signatures_equal'] and (self.args.stage == 'e4' or same_routing):
                        self.result.setdefault('deduplicated', []).append({'budget_gib': gib,
                            'reason': 'identical physical layouts and tested routing; no strategy increment',
                            'routing': 'both e4 routes retained' if self.args.stage == 'e4' else self.args.baseline_routing})
                    measured = []
                    for policy in policies:
                        label = f'{gib}gib_{policy}'
                        routings = (['fastest', 'resource_lp'] if self.args.stage == 'e4' else
                                    [self.args.baseline_routing if policy == 'greedy' else self.args.candidate_routing])
                        policy_row = solution['policies'][policy]
                        pending_keys = [(layout_signature(policy_row['selected']), s,
                            route_signature(policy_row['routing'][r]['scenario_plans'][s]))
                            for r in routings for s in self.slots]
                        needs_build = any(not any(entry['key'] == key for entry in measured) for key in pending_keys)
                        sources = self.build(policy_row['selected'], label, budget) if needs_build else None
                        for routing in routings:
                            for scenario in self.slots:
                                plan = policy_row['routing'][routing]['scenario_plans'][scenario]
                                key = (layout_signature(policy_row['selected']), scenario, route_signature(plan))
                                previous = next((entry for entry in measured if entry['key'] == key), None)
                                if previous is not None:
                                    self.result.setdefault('route_aliases', []).append({
                                        'label': label, 'policy': policy, 'routing': routing, 'scenario': scenario,
                                        'source_phase': previous['phase'], 'new_observation': False,
                                        'reason': 'same layout and identical ordered operation signature',
                                        'original_planning_s': plan['planning_s'],
                                        'placement_solve_wall_s': policy_row['solve_wall_s']})
                                    self.save()
                                    continue
                                self.restore(label, scenario, routing, sources)
                                measured.append({'key': key, 'phase': self.result['cases'][-1]['phase']})
                        self.clear()
            self.result['source_hash_mismatches'] = [p for p, sha in self.hashes.items()
                if hashlib.sha256((ROOT/p).read_bytes()).hexdigest() != sha]
            if self.result['source_hash_mismatches']:
                raise RuntimeError('measured source changed during execution')
            if self.result['status'] != 'decision_only':
                self.result['status'] = 'passed'
        except BaseException as exc:
            self.result.update(status='failed', error=repr(exc))
            raise
        finally:
            if not self.cluster.active:
                try:
                    self.clear()
                except Exception as exc:
                    self.result['cleanup_errors'].append(repr(exc))
            else:
                self.result['cleanup_errors'].append('active transfer retained for explicit diagnosis; no unsafe cleanup')
            self.result['owned_remaining'] = self.owned
            self.result['final_slots'] = self.slots
            try:
                self.result['final_inventory'] = self.cluster.inventory(compact=True)[0]
            except Exception as exc:
                self.result['cleanup_errors'].append('final inventory: '+repr(exc))
            if self.result['cleanup_errors']:
                self.result['status'] = 'failed'
            self.event('run_end', stage=self.args.stage, status=self.result['status'])
            self.save()


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser()
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--stage', choices=['e3', 'e4', 'e5', 'e6'], required=True)
    parser.add_argument('--budget-gib', type=int, default=8)
    parser.add_argument('--budgets-gib', default='0,8,16')
    parser.add_argument('--block-gib', type=int, default=4)
    parser.add_argument('--extra-layouts', action='store_true')
    parser.add_argument('--baseline-routing', choices=['fastest', 'resource_lp'], default='fastest')
    parser.add_argument('--candidate-routing', choices=['fastest', 'resource_lp'], default='resource_lp')
    parser.add_argument('--comparison-run', type=Path,
                        help='passed e4 run whose exact layout/ordered routes may satisfy an unchanged e5 decision')
    args = parser.parse_args()
    if args.budget_gib < 0 or args.block_gib <= 0:
        parser.error('budget must be nonnegative and block size positive')
    MatrixRun(args).run()
