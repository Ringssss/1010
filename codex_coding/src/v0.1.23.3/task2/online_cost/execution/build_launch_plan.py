"""Emit a reviewable real-Qwen role plan; this module never launches processes."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / 'codex_coding/results/v0.1.23.3/task2/online_cost'
TEMPLATE = ROOT / 'codex_coding/results/v0.1.23.3/task2/basic_scenarios/qwen_bootstrap_r01/fresh_engine_node27.process.json'
GiB = 1 << 30


def set_arg(argv, name, value):
    if name in argv:
        argv[argv.index(name) + 1] = str(value)
    else:
        argv.extend([name, str(value)])


def build(run_id, template_path=TEMPLATE):
    if not run_id.startswith('online_') or not all(c.isalnum() or c == '_' for c in run_id):
        raise ValueError('owned online_* session required')
    template = json.loads(template_path.read_text())
    runtime = RESULTS / ('execution_runtime_' + run_id)
    handoff = runtime / 'handoff'
    command_path = runtime / 'engine.command.json'
    env = {k: v for k, v in template['env'].items() if not k.startswith('FLUIDCKPT_')}
    env.update(HIP_VISIBLE_DEVICES='0,1,2,3,4,5,6,7', PYTHONPATH=str(ROOT / 'main_lib'),
               AMD_SERIALIZE_KERNEL='0', NCCL_DEBUG='WARN', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1', RAYON_NUM_THREADS='1', TOKENIZERS_PARALLELISM='false')
    mesh = 'node27:4-5,node30:4-5'
    roles = [('node27', 4, 4), ('node27', 5, 7), ('node30', 4, 4), ('node30', 5, 7)]
    addresses = {'node27': '12.11.3.27', 'node30': '12.11.3.30'}
    topology = {}
    for node in addresses:
        path = ROOT / f'codex_coding/results/v0.1.23.3/task2/multitier_runtime/operations_source_r02/{node}.setup.spec.json'
        topology[node] = json.loads(path.read_text())['topology_input']
    processes = []
    for rank, (node, gpu, numa) in enumerate(roles):
        key = f'worker_{node}_{gpu}'
        role_env = dict(env, NCCL_DEBUG='INFO', NCCL_DEBUG_SUBSYS='INIT,NET', NCCL_TOPO_FILE=topology[node],
                        NCCL_TOPO_DUMP_FILE=str(runtime / 'topology_dump' / f'{key}.xml'))
        argv = ['python3', str(ROOT / 'codex_coding/src/v0.1.23.3/task2/online_cost/execution/worker.py'),
                '--node', node, '--gpu', str(gpu), '--numa', str(numa) if numa is not None else '__FRESH_NUMA_REQUIRED__', '--port-base', '36010',
                '--mesh', mesh, '--master', addresses['node27'] + ':36000', '--handoff', str(handoff),
                '--session', run_id, '--dram-budget', str(48 * GiB), '--hbm-budget', str((3 if node == 'node27' else 1) * GiB),
                '--ssd-budget', str(96 * GiB)]
        processes.append({'key': key, 'kind': 'worker', 'node': node, 'gpu': gpu, 'numa': numa,
                          'worker_rank': rank, 'bdf': ('0000:b5:00.0' if gpu == 5 else '0000:85:00.0'), 'endpoint': f'http://{addresses[node]}:{36010 + gpu}',
                          'argv': argv, 'env': role_env, 'unset_env': ['ROCR_VISIBLE_DEVICES', 'CUDA_VISIBLE_DEVICES'],
                          'container_id': None, 'boot_id': None,
                          'out': str(runtime / ('operations_process_' + key))})
    argv = list(template['command'])
    for name, value in [('--base-gpu-id', 4), ('--port', 36240), ('--nccl-port', 36340), ('--load-format', 'auto')]:
        set_arg(argv, name, value)
    engine_env = dict(env, AMD_SERIALIZE_KERNEL=template['env']['AMD_SERIALIZE_KERNEL'],
                      FLUIDCKPT_MODE='capture', FLUIDCKPT_TAG=run_id + '_bootstrap',
                      FLUIDCKPT_MODEL_ID=template['env']['FLUIDCKPT_MODEL_ID'], FLUIDCKPT_TP_SIZE='2',
                      FLUIDCKPT_HANDOFF=str(handoff), FLUIDCKPT_MANIFEST=str(handoff / (run_id + '_bootstrap') / 'derived.json'),
                      FLUIDCKPT_LIFECYCLE=str(command_path), FLUIDCKPT_METRICS=str(runtime / 'metrics' / 'bootstrap'),
                      FLUIDCKPT_TIMEOUT='1800', FLUIDCKPT_CAPTURE_WAIT='1',
                      NCCL_TOPO_FILE=topology['node27'],
                      NCCL_TOPO_DUMP_FILE=str(runtime / 'topology_dump' / 'engine.xml'))
    processes.append({'key': 'engine_node27', 'kind': 'engine', 'node': 'node27', 'gpus': [4, 5],
                      'argv': argv, 'env': engine_env, 'unset_env': ['ROCR_VISIBLE_DEVICES', 'CUDA_VISIBLE_DEVICES'],
                      'container_id': None, 'boot_id': None, 'out': str(runtime / 'operations_process_engine_node27')})
    nodes = {}
    for node in addresses:
        container = 'zjn-fluid-three-node-' + run_id + '-n' + node[-2:]
        hard = 224 if node == 'node27' else 128
        operational = 192 if node == 'node27' else 96
        nodes[node] = {'container_name': container, 'container_id': None, 'boot_id': None,
                       'container_hard_cap_bytes': hard * GiB, 'task_dram_cap_bytes': operational * GiB,
                       'task_hbm_cap_bytes': 96 * GiB if node == 'node27' else 2 * GiB,
                       'per_selected_gpu_hbm_cap_bytes': 48 * GiB if node == 'node27' else 2 * GiB,
                       'docker_create_resource_args': ['--name', container, '--memory', str(hard) + 'g', '--memory-swap', str(hard) + 'g',
                                                      '--pids-limit', '512', '--shm-size', '1g'],
                       'container_setup_status': 'not_created', 'new_container_required': True,
                       'minimum_host_available_bytes': (256 if node == 'node27' else 128) * GiB,
                       'ongoing_host_available_floor_bytes': (64 if node == 'node27' else 32) * GiB,
                       'minimum_initial_gpu_free_bytes': (52 * GiB if node == 'node27' else (2 * GiB + (256 << 20)))}
    return {'schema': 'online_cost_launch.v1', 'development': 'v0.1.23.3', 'task': 'task2',
            'run_id': run_id, 'main_approved': False, 'fresh_admission_passed': False,
            'budget_status': 'main_explicit_two_node_target192_helper96_pending_fresh_gate', 'lifetime_s': 7200,
            'phase_deadlines_s': {'setup': 300, 'admission': 300, 'init': 180, 'bootstrap': 1800, 'calibrate': 1800, 'validate': 1800, 'cleanup': 180, 'collection': 180},
            'aggregate_budget': {'dram': 192 * GiB},
            'aggregate_budget_by_node': {n: {'dram': r['task_dram_cap_bytes']} for n, r in nodes.items()},
            'nodes': nodes, 'processes': processes,
            'engine_command_path': str(command_path), 'handoff': str(handoff),
            'model_identity': template['env']['FLUIDCKPT_MODEL_ID'], 'tp_size': 2,
            'model_path': argv[argv.index('--model-path') + 1], 'weights_bytes_by_rank': [35164859056] * 2,
            'persistent_local_hbm_bytes_by_rank': [GiB] * 2,
            'template_sha256': hashlib.sha256(template_path.read_bytes()).hexdigest(),
            'runtime_sources_not_rebound': True, 'source_reuse_status': 'fresh capture required; no live owner assumption',
            'pending': ['fresh metadata/config access', 'GPU4/5 BDF/NUMA and free memory gate',
                        'helper hardware gate', 'main final budget', 'current XML acceptance incl GPU5',
                        'fresh own-container creation and exact identity binding', 'trusted processed source setup',
                        'initial legal full restore and park', 'frozen cost prediction and online case specification'],
            'scope': 'two-node four-GPU role launcher; shared source and target NIC; no model sample or three-node claim', 'hardware_scope': 'node27 targets GPU4/5 and node30 helper GPU4/5 only'}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--run-id', required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--template', type=Path, default=TEMPLATE)
    a = p.parse_args()
    out = a.out.resolve()
    if out.parent != RESULTS or not out.name.startswith('execution_plan_'):
        raise ValueError('new execution_plan_* result directory required')
    row = build(a.run_id, a.template)
    out.mkdir(parents=True, exist_ok=False)
    (out / 'launch_plan.json').write_text(json.dumps(row, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'status': 'plan_only', 'path': str(out / 'launch_plan.json'), 'main_approved': False}))


if __name__ == '__main__':
    main()
