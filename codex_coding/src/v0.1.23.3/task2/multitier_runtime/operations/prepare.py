"""Freeze isolated setup from the proven SYS_NICE wrapper; never execute setup."""
import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[6]
HERE = Path(__file__).resolve().parent
RESULTS = ROOT / 'codex_coding/results/v0.1.23.3/task2/multitier_runtime'
OLD = ROOT / 'codex_coding/src/v0.1.23.3/task2/three_node_recovery/inventory/fresh_host.py'
MAPPING = ROOT / 'codex_coding/results/v0.1.23.3/task2/multitier_scenarios'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, body):
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + '\n')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--run', default='multitier_runtime_r01')
    a = p.parse_args()
    a.out = a.out.resolve()
    if a.out.parent != RESULTS or not a.out.name.startswith('operations_') or not re.fullmatch('[a-z0-9_]+', a.run):
        raise ValueError('new owned operations result/run required')
    a.out.mkdir(parents=True, exist_ok=False)
    source = OLD.read_text()
    source = source.replace('("node28","node30")', '("node27","node28","node30")')
    source = source.replace('(28|30)', '(27|28|30)')
    source = source.replace('"--cap-add","IPC_LOCK","--ulimit"', '"--cap-add","IPC_LOCK","--cap-add","SYS_NICE","--ulimit"')
    source = source.replace('not str(rel).startswith("codex_coding/")', 'not str(rel).startswith(("codex_coding/", "main_lib/fluidckpt/"))')
    source = source.replace('if mode=="guard":\n        result=guard(spec)', 'if mode=="guard":\n        raise SystemExit("use safe operations host_readback.py instead")\n        result=guard(spec)')
    source = source.replace('stage.mkdir(parents=True,exist_ok=False)',
        'ssd=Path(spec["ssd_directory"])\n    if ssd.parent != Path(spec["ssd_parent"]) or ssd.name != spec["run"]:\n        raise SystemExit("SSD own directory mismatch")\n    ssd.mkdir(parents=False,exist_ok=False)\n    stage.mkdir(parents=True,exist_ok=False)')
    source = source.replace('"--workdir",PROJECT,IMAGE,"infinity"]',
        '"--mount",f"type=bind,src={ssd},dst=/task_ssd","--workdir",PROJECT,IMAGE,"infinity"]')
    assert source.count('"--cap-add","SYS_NICE"') == 1
    wrapper = source.encode()
    (a.out / 'setup_host.source.py').write_bytes(wrapper)
    binding = json.loads((MAPPING / 'mapping_binding_r03/machine_binding.json').read_text())
    sys.path.insert(0, str(ROOT / 'main_lib'))
    from fluidckpt.topology.rccl_xml import physical_xml
    paths = set((ROOT / 'main_lib/fluidckpt').rglob('*.py'))
    for rel in ('codex_coding/src/v0.1.23.3/task2/multitier_scenarios/adapter',
                'codex_coding/src/v0.1.23.3/task2/multitier_runtime/runner',
                'codex_coding/src/v0.1.23.3/task2/multitier_runtime/operations'):
        paths.update((ROOT / rel).rglob('*.py'))
    paths.add(ROOT / 'codex_coding/src/v0.1.23.3/task2/three_node_recovery/admission28/runtime_gate_r03.py')
    paths.add(MAPPING / 'contract_cpu_r02/contract.json')
    assets = []
    for path in sorted(paths):
        raw = path.read_bytes()
        assets.append(dict(relative_path=str(path.relative_to(ROOT)), sha256=sha(raw), base64=base64.b64encode(raw).decode()))
    specs = []
    for n, host in enumerate(binding['hosts']):
        node = host['node']
        if node == 'node27':
            metadata = json.loads((MAPPING / 'mapping_refresh_r01/node27.stdout.json').read_text())
            pci = json.loads((MAPPING / 'mapping_pci_followup_r01/node27.stdout.json').read_text())
            metadata['kfd'] = pci['gpus']
            xml, policy = physical_xml(metadata, gdr=0)
        else:
            xml = (ROOT / f'codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_xml_2830_gdr0_r01/{node}.physical.gdr0.xml').read_bytes()
            policy = {'reuse_verified_seed': True, 'runtime_accepted': False}
        xmlpath = a.out / (node + '.physical.gdr0.xml')
        xmlpath.write_bytes(xml)
        write(xmlpath.with_suffix('.policy.json'), policy)
        xmlrel = str(xmlpath.relative_to(ROOT))
        nodeassets = assets + [dict(relative_path=xmlrel, sha256=sha(xml), base64=base64.b64encode(xml).decode())]
        parent = '/data/' + node + '/shared' if node != 'node27' else str(RESULTS)
        roles = [dict(gpu=r['historical_ordinal'], bdf=r['bdf'], numa=r['numa'], logical_gpu=r['logical_gpu']) for r in host['roles']]
        known = []
        if node != 'node27':
            prior = ROOT / 'codex_coding/results/v0.1.23.3/task2/minimal_calibration/measure_integration_deploy_r01' / (node+'.latest.guard.spec.json')
            known = json.loads(prior.read_text()).get('known_live_workers', [])
        spec = dict(node=node, hostname=host['hostname'], boot_id=host['boot_id'], run=a.run,
                    image_id='sha256:ee3b9807d9e90cdc3e6ab9c1058f0b78dabcafa6a0fc7661d9c5af69ce1f9588',
                    container_name=f'zjn-fluid-three-node-{a.run}-n{node[-2:]}',
                    stage=f'/tmp/fluidckpt_three_node/{a.run}_n{node[-2:]}',
                    gpu=roles[0]['gpu'], bdf=roles[0]['bdf'], roles=roles,
                    ports=[35100+r['gpu'] for r in roles]+[35400+n]+([29981] if node == 'node28' else []),
                    ssd_port=35400+n, ssd_parent=parent, ssd_directory=parent+'/'+a.run,
                    logical_node=host['logical_node'], assets=nodeassets, topology_input=str(xmlpath),
                    host_wrapper_sha256=sha(wrapper), physical_ready=False,
                    experiment_increment_budget={'dram': 6 << 30, 'hbm': 2 << 30, 'container': 8 << 30, 'postwarm_application_margin': 256 << 20},
                    authorization='parent executes setup only; no GPU startup', known_live_workers=known, keeper_endpoints=[])
        write(a.out / (node + '.setup.spec.json'), spec)
        specs.append(str(a.out / (node + '.setup.spec.json')))
    write(a.out / 'manifest.json', dict(specs=specs, session=a.run, GPU_launches=0, remote_writes=0,
          wrapper_sha256=sha(wrapper), reused_wrapper_sha256=sha(OLD.read_bytes()), asset_count=len(assets)))
    print(json.dumps({'out': str(a.out), 'specs': specs, 'GPU_launches': 0}))


if __name__ == '__main__':
    main()
