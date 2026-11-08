"""Exercise immutable staging, hostile archive refusal and phase dispatch on CPU."""
import argparse
import base64
import copy
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

from prepare import RESULTS, ROOT, digest
from orchestrate import HostOps
from simple_host import SimpleHostOps
from outer_host import OuterHostOps, require_outer
from outer_prepare import build_outer
from outer_bundle import (authenticated_files, bundle_assets, MODEL_NAME, RAW_NAMES, OLD_PHASE,
                          OLD_RUN, SOURCE_PLAN, SOURCE_PLAN_SHA256, MODEL_SHA256)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.parent != RESULTS or not out.name.startswith('outer_ops_cpu_'):
        raise ValueError('new outer_ops_cpu_* output required')
    out.mkdir(parents=True, exist_ok=False)
    checks = []
    def check(name, condition):
        checks.append(dict(name=name, passed=bool(condition)))
    def rejects(name, call):
        try:
            call()
        except (ValueError, KeyError):
            check(name, True)
        else:
            check(name, False)
    original_files = authenticated_files()
    manifest, assets, frozen = bundle_assets(args.bundle)
    check('whitelist_exact19_no_C0_or_holdouts', len(assets) == 19 and set(original_files) == {MODEL_NAME, *RAW_NAMES})
    check('whole_frozen_plan_exact', digest(SOURCE_PLAN.read_bytes()) == SOURCE_PLAN_SHA256)
    check('old_checkpoint_authentication', digest(original_files[MODEL_NAME]) == MODEL_SHA256)
    check('asset_bytes_match_archive', all(base64.b64decode(asset['base64']) == original_files[Path(asset['relative_path']).name] for asset in assets))
    check('original_logical_absolute_paths_preserved', {str(ROOT / asset['relative_path']) for asset in assets} == {str(OLD_PHASE / name) for name in original_files})
    check('bounded_payload', manifest['total_bytes'] == sum(map(len, original_files.values())) < 32 << 20)
    check('source_checkpoint_remains_unqualified', json.loads(original_files[MODEL_NAME])['online_outer_s'] is None)
    plan, copied = build_outer('online_outer_cpu_r01', args.bundle)
    check('four_native_roles', [row['key'] for row in plan['processes']] == [row['key'] for row in frozen['processes']])
    check('target192_224_48_and_minfree52_unchanged', plan['nodes']['node27']['task_dram_cap_bytes'] == 192 << 30
          and plan['nodes']['node27']['container_hard_cap_bytes'] == 224 << 30
          and plan['nodes']['node27']['per_selected_gpu_hbm_cap_bytes'] == 48 << 30
          and plan['nodes']['node27']['minimum_initial_gpu_free_bytes'] == 52 << 30)
    check('helper96_128_planned2_and_free4_2_unchanged', plan['nodes']['node30']['task_dram_cap_bytes'] == 96 << 30
          and plan['nodes']['node30']['container_hard_cap_bytes'] == 128 << 30
          and plan['nodes']['node30']['task_hbm_cap_bytes'] == 2 << 30
          and plan['nodes']['node30']['minimum_initial_gpu_free_bytes'] == 4 << 30
          and plan['nodes']['node30']['ongoing_gpu_free_floor_bytes'] == 2 << 30)
    check('old_identity_not_live_reused', all(row['container_id'] is None and row['boot_id'] is None for row in plan['processes']))
    check('runtime_run_rebound', all(OLD_RUN not in json.dumps(row) for row in plan['processes']) and 'online_outer_cpu_r01' in plan['handoff'])
    check('old_checkpoint_ref_not_rebound', plan['outer_protocol']['source_checkpoint_path_model_ref'] == manifest['source_checkpoint_path_model_ref'])
    check('topology_inputs_unchanged', [row['env']['NCCL_TOPO_FILE'] for row in plan['processes']] == [row['env']['NCCL_TOPO_FILE'] for row in frozen['processes']])
    check('only_sequence_online_phase', OuterHostOps.RUN_PHASES == ('bootstrap', 'sequence'))
    check('old_host_default_phases_unchanged', HostOps.RUN_PHASES == SimpleHostOps.RUN_PHASES == ('bootstrap', 'calibrate', 'validate'))
    mutated = copy.deepcopy(plan)
    mutated['outer_protocol']['complete_request_order'].reverse()
    rejects('request_order_change_refused', lambda: require_outer(mutated))
    mutated = copy.deepcopy(plan)
    mutated['outer_protocol']['max_complete_requests'] = 5
    rejects('fifth_request_refused', lambda: require_outer(mutated))
    mutated = copy.deepcopy(plan)
    mutated['outer_protocol']['outer_domain'] = 'first_controller'
    rejects('mixed_controller_domain_refused', lambda: require_outer(mutated))
    class Capture(OuterHostOps):
        def __init__(self):
            self.calls = []
            self.events = []
        def journal(self, *args, **kwargs):
            self.events.append((args, kwargs))
        def record(self, node, argv, name, timeout):
            self.calls.append(dict(node=node, argv=argv, name=name, timeout=timeout))
    capture = Capture()
    plan['nodes']['node27']['container_id'] = 'a' * 64
    for action in ('init', 'bootstrap', 'sequence', 'cleanup'):
        capture.controller(plan, Path(plan['handoff']).parent / 'launch_plan.json', action)
    check('four_controller_invocations_only_one_online', len(capture.calls) == 4 and sum(call['name'] == 'controller_sequence' for call in capture.calls) == 1)
    sequence = capture.calls[2]
    check('sequence_same_out_single_deadline', '--action' in sequence['argv'] and 'sequence' in sequence['argv']
          and str(Path(plan['handoff']).parent / 'phase_sequence') in sequence['argv'] and sequence['timeout'] == 3615)
    rejects('separate_calibrate_process_refused', lambda: capture.controller(plan, Path('/unused'), 'calibrate'))
    rejects('separate_validate_process_refused', lambda: capture.controller(plan, Path('/unused'), 'validate'))
    check('journal_does_not_claim_separate_validation', all(fields.get('separate_validate_process_executed') is False for (phase, state), fields in capture.events if state == 'complete'))
    with tempfile.TemporaryDirectory() as temporary:
        temporary = Path(temporary)
        def archive_test(name, alter, extra=None):
            path = temporary / (name + '.tar')
            with tarfile.open(path, 'w:') as tar:
                for filename, raw in original_files.items():
                    member = tarfile.TarInfo('phase_calibrate/' + filename)
                    member.size = len(raw)
                    replacement = alter(filename, member, raw)
                    if replacement is not None:
                        member, raw = replacement
                    tar.addfile(member, io.BytesIO(raw) if member.isfile() else None)
                if extra:
                    extra(tar)
            return path, digest(path.read_bytes())
        def symlink(name, member, raw):
            if name == RAW_NAMES[0]:
                member.type = tarfile.SYMTYPE
                member.linkname = '/etc/passwd'
                member.size = 0
            return member, raw
        path, expected = archive_test('symlink_refused', symlink)
        rejects('archive_whitelisted_symlink_refused', lambda: authenticated_files(path, expected))
        def hardlink(name, member, raw):
            if name == RAW_NAMES[0]:
                member.type = tarfile.LNKTYPE
                member.linkname = 'phase_calibrate/' + RAW_NAMES[1]
                member.size = 0
            return member, raw
        path, expected = archive_test('hardlink_refused', hardlink)
        rejects('archive_whitelisted_hardlink_refused', lambda: authenticated_files(path, expected))
        def duplicate(tar):
            raw = original_files[RAW_NAMES[0]]
            member = tarfile.TarInfo('phase_calibrate/' + RAW_NAMES[0])
            member.size = len(raw)
            tar.addfile(member, io.BytesIO(raw))
        path, expected = archive_test('duplicate_refused', lambda *args: None, duplicate)
        rejects('archive_duplicate_refused', lambda: authenticated_files(path, expected))
        path, expected = archive_test('tampered_raw_refused', lambda name, member, raw:
            (member, b'X' + raw[1:]) if name == RAW_NAMES[0] else None)
        rejects('archive_raw_ref_hash_mismatch_refused', lambda: authenticated_files(path, expected))
        rejects('archive_external_sha_mismatch_refused', lambda: authenticated_files(path, '0' * 64))
        def unsafe(tar):
            member = tarfile.TarInfo('../../OUTSIDE')
            member.size = 1
            tar.addfile(member, io.BytesIO(b'X'))
        path, expected = archive_test('unlisted_unsafe_path_ignored', lambda *args: None, unsafe)
        check('unlisted_archive_path_never_extracted', authenticated_files(path, expected) == original_files and not (temporary / 'OUTSIDE').exists())
    result = subprocess.run(['python3', str(Path(__file__).parent / 'outer_host.py'), 'run', '--setup-dir', '/unused', '--out', str(RESULTS / 'execution_host_outer_cpu_noexecute')], capture_output=True, text=True)
    check('noexecute_no_GPU_no_remote_no_output', result.returncode == 0 and json.loads(result.stdout)['status'] == 'not_executed'
          and not (RESULTS / 'execution_host_outer_cpu_noexecute').exists())
    report = dict(passed=all(check['passed'] for check in checks), checks=checks, GPU_launches=0, remote_writes=0,
                  bundle_ref=dict(path=str(args.bundle.resolve() / 'manifest.json'), sha256=digest((args.bundle / 'manifest.json').read_bytes())))
    (out / 'qualification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(passed=report['passed'], checks=len(checks), out=str(out), GPU_launches=0, remote_writes=0)))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
