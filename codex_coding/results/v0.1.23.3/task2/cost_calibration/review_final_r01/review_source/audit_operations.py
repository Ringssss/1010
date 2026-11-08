"""Review concrete owned cleanup, pair logs, and same-HIP budget evidence."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[6]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    refs, checks = [], []

    def freeze(path):
        path = path.resolve()
        raw = path.read_bytes()
        dest = args.out / 'evidence' / path.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        refs.append(dict(source_path=str(path), path=str(dest), sha256=hashlib.sha256(raw).hexdigest()))
        return raw

    def load(path):
        return json.loads(freeze(path))

    def check(name, condition):
        checks.append(dict(name=name, passed=bool(condition)))

    run = args.run_dir
    summary = load(run / 'summary.json')
    plan = load(Path(summary['plan_ref']['path']))
    check('plan_hash', hashlib.sha256(Path(summary['plan_ref']['path']).read_bytes()).hexdigest() == summary['plan_ref']['sha256'])
    mesh = load(run / 'mesh/mesh.json')
    budget = load(run / 'budget_evidence.json')
    monitor = load(run / 'budget_monitor.json')
    communicators = defaultdict(list)
    maxima = {}
    pattern = re.compile(r':([0-9]+):[0-9]+ \[[0-9]+\] NCCL INFO ncclCommInitRank comm (0x[0-9a-f]+) rank ([0-9]+) nranks ([0-9]+) cudaDev ([0-9]+) nvmlDev ([0-9]+) busId ([0-9a-f]+) commId (0x[0-9a-f]+) - Init START')
    for row in plan['processes']:
        directory = run / ('artifacts_' + row['node']) / 'raw' / ('operations_process_' + plan['session'] + '_' + row['key'])
        supervisor = load(directory / 'supervisor.summary.json')
        owner = load(directory / 'child.owner.json')
        log = freeze(directory / 'child.log').decode(errors='replace')
        check(row['key'] + '_planned_TERM_only', supervisor['passed'] and supervisor['stop_reason'] == 'owned_stop_marker' and
              supervisor['child_returncode'] == -15 and supervisor['cleanup']['signals'] == ['TERM'] and
              not supervisor['cleanup']['pending_group_members'] and supervisor['plan_sha256'] == summary['plan_ref']['sha256'])
        maxima[row['node']] = max(maxima.get(row['node'], 0),
            max(int(sample['memory.current']) for sample in supervisor['samples'] if str(sample.get('memory.current', '')).isdigit()))
        if row['kind'] == 'worker':
            worker = mesh['workers'][row['worker']]
            check(row['key'] + '_actual_owner', owner['pid'] == worker['runtime_identity']['pid'] and
                  int(owner['start_time']) == worker['runtime_identity']['start_ticks'] and worker['gpu'] == row['gpu'])
            check(row['key'] + '_no_RCCL_warning', 'NCCL WARN' not in log)
            domain, bus, device_function = worker['runtime_identity']['actual_bdf'].split(':')
            device, function = device_function.split('.')
            bdf = (int(domain, 16) << 20) | (int(bus, 16) << 12) | (int(device, 16) << 4) | int(function, 16)
            for match in pattern.finditer(log):
                pid, ptr, subgroup, nranks, gpu, nvml, address, cid = match.groups()
                check(row['key'] + '_communicator_owner_' + cid, int(pid) == owner['pid'] and
                      int(gpu) == worker['gpu'] and int(address, 16) == bdf and int(nranks) == 2)
                communicators[cid].append(dict(worker=row['worker'], subgroup_rank=int(subgroup), pointer=ptr))
            if row['worker'] in (2, 4):
                check(row['key'] + '_cross_node_IBext_gdr0', all(token in log for token in
                      ('Using network IBext_v8', 'shca_0:1/IB', 'GPU Direct RDMA Disabled')))
    expected = {tuple(pair) for pair in mesh['necessary_pairs']}
    actual = {tuple(sorted(item['worker'] for item in group)) for group in communicators.values()}
    check('only_two_necessary_pair_communicators', actual == expected == {(2, 4), (4, 5)} and
          len(communicators) == 2 and all(len(group) == 2 and {item['subgroup_rank'] for item in group} == {0, 1}
          for group in communicators.values()))
    for node in ('node28', 'node30', 'node27'):
        exact = load(run / ('exact_collect_' + node) / 'stdout.json')
        after = load(run / ('after_stop_' + node) / 'stdout.json')
        check(node + '_exact_absent_empty_quiet', exact['passed'] and len(exact['reports']) == 3 and
              all(not item['current']['live'] and not item['group'] for item in exact['reports']) and
              after['cgroup']['files']['pids.current'] == '1' and all(after['ports_free'].values()))
        check(node + '_sampled_DRAM_plus_margin', maxima[node] + (256 << 20) <= 6 << 30)
        check(node + '_SYS_NICE_privateIPC', 'SYS_NICE' in after['container']['HostConfig']['CapAdd'] and
              after['container']['HostConfig']['IpcMode'] == 'private')
        check(node + '_postwarm_absolute_HIP', budget['nodes'][node]['hbm_absolute_upper_bytes'] +
              budget['nodes'][node]['planned_incremental_upper_bytes'] <= 2 << 30)
    check('live_monitor_retained_identity_and_budget', bool(monitor) and all(
          row['require_idle'] is False and 'transactions' in row and
          row['dram_current_bytes'] + (256 << 20) <= row['dram_limit_bytes'] == 6 << 30 and
          row['hbm_absolute_upper_bytes'] <= row['hbm_limit_bytes'] == 2 << 30
          for sample in monitor for row in sample['nodes'].values()))
    result = dict(status='passed' if all(row['passed'] for row in checks) else 'failed', checks=checks,
        communicators=dict(communicators), sampled_DRAM_max_bytes=maxima,
        sampled_HIP_max_bytes={node: max(sample['nodes'][node]['hbm_absolute_upper_bytes'] for sample in monitor)
                              for node in ('node28', 'node30', 'node27')},
        source_refs=refs, CPU_only=True, physical_peak_measured=False, shared_capacity_measured=False,
        protection_review='separately reuse multitier_runtime/review/audit_protection.py')
    (args.out / 'qualification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(dict(status=result['status'], checks=len(checks), sampled_HIP_max_bytes=result['sampled_HIP_max_bytes'])))
    if result['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
