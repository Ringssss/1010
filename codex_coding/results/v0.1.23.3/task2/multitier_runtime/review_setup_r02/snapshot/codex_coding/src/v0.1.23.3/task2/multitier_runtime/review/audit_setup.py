"""Review frozen isolated setup and metadata; never execute remote commands."""
import argparse
import ast
import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / 'codex_coding/results/v0.1.23.3/task2/multitier_runtime'
MAPPING = ROOT / 'codex_coding/results/v0.1.23.3/task2/multitier_scenarios/mapping_binding_r03/machine_binding.json'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--setup-dir', type=Path, required=True)
    p.add_argument('--preflight', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    snapshot = a.out / 'snapshot'
    snapshot.mkdir()
    refs, checks = [], []
    def freeze(path):
        path = path.resolve()
        raw = path.read_bytes()
        target = snapshot / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        refs.append(dict(source_path=str(path), path=str(target), sha256=digest(raw)))
        return raw
    def check(name, ok):
        checks.append(dict(name=name, passed=bool(ok)))
    binding = json.loads(freeze(MAPPING))
    wrapper_raw = freeze(a.setup_dir / 'setup_host.source.py')
    wrapper = wrapper_raw.decode()
    ast.parse(wrapper)
    check('isolated_setup_no_GPU_program', 'if mode!="setup"' in wrapper and '"infinity"]' in wrapper)
    check('no_overwrite_stage_or_extents', 'stage.exists()' in wrapper and 'ssd.mkdir(parents=False,exist_ok=False)' in wrapper)
    check('permissions', '"--ipc","private"' in wrapper and '"--cap-add","SYS_NICE"' in wrapper and '"--privileged"' not in wrapper)
    for host in binding['hosts']:
        node = host['node']
        spec = json.loads(freeze(a.setup_dir / (node + '.setup.spec.json')))
        before_path = a.preflight / node / 'stdout.json'
        if not before_path.exists():
            before_path = a.preflight / ('before_' + node) / 'stdout.json'
        before = json.loads(freeze(before_path))
        wanted = [(r['historical_ordinal'], r['bdf'], r['numa']) for r in host['roles']]
        actual = [(r['gpu'], r['bdf'], r['numa']) for r in spec['roles']]
        check(node + '_exact_mapping_boot', actual == wanted and before['boot_id'] == spec['boot_id'] == host['boot_id'])
        check(node + '_fresh_name_ports', not before['container'].get('Id') and all(before['ports_free'].values()))
        check(node + '_owned_directories', spec['stage'].startswith('/tmp/fluidckpt_three_node/') and
              Path(spec['ssd_directory']).parent == Path(spec['ssd_parent']) and Path(spec['ssd_directory']).name == spec['run'])
        check(node + '_SSD_parent_writable', before['ssd_parent'].get('writable_executable') is True)
        check(node + '_setup_source_sha', spec['host_wrapper_sha256'] == digest(wrapper_raw))
        valid_assets = True
        for asset in spec['assets']:
            rel = Path(asset['relative_path'])
            valid_assets = valid_assets and not rel.is_absolute() and '..' not in rel.parts
            valid_assets = valid_assets and str(rel).startswith(('main_lib/fluidckpt/', 'codex_coding/'))
            valid_assets = valid_assets and digest(base64.b64decode(asset['base64'], validate=True)) == asset['sha256']
        check(node + '_frozen_assets', valid_assets)
        check(node + '_aggregate_cap_contract', spec['experiment_increment_budget']['dram'] == 6 << 30 and
              spec['experiment_increment_budget']['hbm'] == 2 << 30 and
              spec['experiment_increment_budget']['postwarm_application_margin'] == 256 << 20)
        check(node + '_protection_and_CPU_preflight', bool(before['protected_containers']) and
              'original_xml_sha256' in before and before['gpu_api_calls'] == 0 and before['signals'] == [] and
              before['remote_writes'] == 0 and before['node29_accesses'] == 0)
        xml = freeze(Path(spec['topology_input']))
        check(node + '_task_XML', str(Path(spec['topology_input']).resolve()).startswith(str(a.setup_dir.resolve()) + '/') and b'<system' in xml)
    freeze(Path(__file__))
    report = dict(status='passed' if all(c['passed'] for c in checks) else 'failed',
                  clearance='isolated CPU setup only; no GPU launch clearance', checks=checks,
                  source_refs=refs, actual_six_worker_certified=False, physical_peak_measured=False,
                  caveats=['postsetup old owner/XML identity comparison remains required',
                           'host absent factory XML is not the container copy SHA',
                           'final runner/operations source must be refreshed and frozen before GPU launch'])
    (a.out / 'qualification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(status=report['status'], clearance=report['clearance'], checks=len(checks))))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
