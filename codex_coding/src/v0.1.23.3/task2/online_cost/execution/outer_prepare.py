"""Freeze fresh outer-validation deployment with authenticated r02 parameters."""
import argparse
import base64
import copy
import json
from pathlib import Path
import re

from prepare import ROOT, RESULTS, UPSTREAM, IMAGE, derive_setup, digest
from outer_bundle import OLD_RUN, bundle_assets
from outer_host import PROTOCOL, require_outer
from simple_resources import numa_plan_checks


def build_outer(run_id, bundle):
    if not re.fullmatch('online_outer_[a-z0-9_]+', run_id):
        raise ValueError('fresh owned online_outer_* run required')
    manifest, inherited_assets, frozen = bundle_assets(bundle)
    def rebind(value):
        if isinstance(value, str):
            return value.replace(OLD_RUN, run_id)
        if isinstance(value, list):
            return [rebind(item) for item in value]
        if isinstance(value, dict):
            return {key: rebind(item) for key, item in value.items()}
        return value
    plan = rebind(copy.deepcopy(frozen))
    for key in ('setup_specs', 'source_assets_sha256', 'model_metadata_source_ref', 'model_metadata_sha256'):
        plan.pop(key, None)
    plan['phase_deadlines_s']['sequence'] = 3600
    plan['outer_protocol'] = dict(schema=PROTOCOL,
        source_checkpoint_path_model_ref=copy.deepcopy(manifest['source_checkpoint_path_model_ref']),
        source_frozen_plan_ref=copy.deepcopy(manifest['source_frozen_plan_ref']),
        source_bundle_ref=dict(path=str(Path(bundle).resolve() / 'manifest.json'),
                               sha256=digest((Path(bundle) / 'manifest.json').read_bytes())),
        prime_run_id='C0_prime', outer_calibration_run_id='C0_cal',
        complete_request_order=['C0_prime', 'C0_cal', 'Hremote', 'Hnear'], validation_order=['Hremote', 'Hnear'],
        max_complete_requests=4, outer_domain='post_one_fullrestore_samecontroller',
        path_calibration_requests=0, topology_calibration_requests=0,
        inherited_path_evidence='original r02 exact bytes/context/session retained; current runtime identity independently rebound')
    plan.update(scope='two-node three-GPU outer-only calibration; one persistent controller process for all four complete requests',
                main_approved=False, fresh_admission_passed=False,
                runtime_sources_not_rebound=True,
                source_reuse_status='old path-cost parameters only; fresh capture/owners/epochs required')
    require_outer(plan)
    return plan, inherited_assets


def freeze(args):
    out = args.out.resolve()
    if out.parent != RESULTS or not out.name.startswith('execution_setup_outer_'):
        raise ValueError('new execution_setup_outer_* output required')
    plan, inherited_assets = build_outer(args.run_id, args.bundle)
    model = json.loads(args.model_metadata.read_text())
    expected = {key: model['metadata'][key]['sha256'] for key in ('config.json', 'model.safetensors.index.json')}
    plan['model_metadata_sha256'] = expected
    plan['model_metadata_source_ref'] = dict(path=str(args.model_metadata.resolve()), sha256=digest(args.model_metadata.read_bytes()))
    wrapper, changes = derive_setup()
    paths = set((ROOT / 'main_lib/fluidckpt').rglob('*.py'))
    for relative in ('codex_coding/src/v0.1.23.3/task2/online_cost',
                     'codex_coding/src/v0.1.23.3/task2/multitier_runtime/runner',
                     'codex_coding/src/v0.1.23.3/task2/multitier_runtime/operations',
                     'codex_coding/src/v0.1.23.3/task2/minimal_calibration/measure'):
        paths.update((ROOT / relative).rglob('*.py'))
        paths.update((ROOT / relative).rglob('*.json'))
    paths.add(ROOT / 'codex_coding/src/serving/sglang_fluid_launch.py')
    paths.add(ROOT / 'codex_coding/src/v0.1.23.3/task2/three_node_recovery/admission28/runtime_gate_r03.py')
    for process in plan['processes']:
        paths.add(Path(process['env']['NCCL_TOPO_FILE']))
    assets = []
    for path in sorted(paths):
        raw = path.read_bytes()
        assets.append(dict(relative_path=str(path.relative_to(ROOT)), sha256=digest(raw), base64=base64.b64encode(raw).decode()))
    assets.extend(inherited_assets)
    if len({asset['relative_path'] for asset in assets}) != len(assets):
        raise ValueError('duplicate deployment asset mapping')
    inputs = {process['env']['NCCL_TOPO_FILE'] for process in plan['processes']}
    dumps = {process['env']['NCCL_TOPO_DUMP_FILE'] for process in plan['processes']}
    if inputs & dumps:
        raise ValueError('topology input and dump paths overlap')
    specs = {}
    for node, budgets in plan['nodes'].items():
        metadata_path = args.metadata_root / node / 'stdout.json'
        metadata = json.loads(metadata_path.read_text())
        roles = [{key: role[key] for key in ('gpu', 'bdf', 'numa')} for role in metadata['selected_gpus']]
        for role in roles:
            role['host_numa'] = role['numa']
        if (not metadata['host_memory_admission']['passed'] or not metadata['requested_image_present']
                or not metadata['candidate_name_free'] or not all(metadata['ports_free'].values())):
            raise ValueError('fresh metadata setup gates failed: ' + node)
        expected_roles = {(row['gpu'], row['bdf'], row['numa']) for row in plan['processes'] if row['kind'] == 'worker' and row['node'] == node}
        if {(row['gpu'], row['bdf'], row['numa']) for row in roles} != expected_roles:
            raise ValueError('fresh physical roles do not match frozen approved parameters: ' + node)
        checks = numa_plan_checks(metadata['numa_memory'], plan['planned_source_bytes_by_numa'][node])
        if not all(row['passed'] for row in checks):
            raise ValueError('fresh selected source NUMA capacity failed: ' + node)
        ports = [36014, 36015, 36000, 36240, 36340] if node == 'node27' else [36010 + row['gpu'] for row in roles]
        specs[node] = dict(budgets, node=node, hostname=metadata['hostname'], boot_id=metadata['boot_id'],
            run=args.run_id, stage=f'/tmp/fluidckpt_three_node/{args.run_id}_n{node[-2:]}', image_id=IMAGE,
            roles=roles, ports=ports, assets=assets, host_wrapper_sha256=digest(wrapper),
            model_path=plan['model_path'], model_metadata_sha256=expected,
            authorization='new container setup only; zero GPU starts; old frozen path assets only',
            metadata_source_ref=dict(path=str(metadata_path.resolve()), sha256=digest(metadata_path.read_bytes())),
            gpu=roles[0]['gpu'], bdf=roles[0]['bdf'], known_live_workers=[], keeper_endpoints=[])
    out.mkdir(parents=True, exist_ok=False)
    (out / 'setup_host.source.py').write_bytes(wrapper)
    for node, spec in specs.items():
        (out / (node + '.setup.spec.json')).write_text(json.dumps(spec, indent=2) + '\n')
        (out / (node + '.bootstrap.payload.json')).write_text(json.dumps(dict(spec=spec, wrapper=base64.b64encode(wrapper).decode())) + '\n')
    plan['setup_specs'] = {node: str(out / (node + '.setup.spec.json')) for node in specs}
    plan['source_assets_sha256'] = {asset['relative_path']: asset['sha256'] for asset in assets}
    (out / 'launch_plan.unbound.json').write_text(json.dumps(plan, indent=2) + '\n')
    provenance = dict(upstream=str(UPSTREAM), upstream_sha256=digest(UPSTREAM.read_bytes()), derived_sha256=digest(wrapper),
        changes=changes, model_mount='explicit read-only original model directory', GPU_launches=0, remote_writes=0,
        asset_count=len(assets), inherited_path_artifact_count=19,
        outer_protocol=plan['outer_protocol'], frozen_parameter_source_ref=plan['outer_protocol']['source_frozen_plan_ref'])
    (out / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    return dict(status='outer_setup_frozen_not_executed', out=str(out), asset_count=len(assets), inherited_path_artifacts=19)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    for name in ('out', 'metadata-root', 'model-metadata', 'bundle'):
        parser.add_argument('--' + name, type=Path, required=True)
    print(json.dumps(freeze(parser.parse_args())))


if __name__ == '__main__':
    main()
