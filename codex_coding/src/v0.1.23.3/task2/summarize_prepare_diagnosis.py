"""Preserve evidence and unresolved boundaries; do not turn a hypothesis into a root cause."""
import argparse
import json
from pathlib import Path
import pickletools

ROOT = Path(__file__).resolve().parents[4]


def main(out):
    results = ROOT/'codex_coding/results/v0.1.23.3/task2'
    full = json.loads((results/'prepareprofile01/summary.json').read_text())
    assert full['status'] == 'passed' and not full['cleanup_errors'] and not full['source_hash_mismatches']
    profiles = json.loads((results/'prepareprofile01/prepare_analysis.json').read_text())
    micro = [json.loads((results/name/'summary.json').read_text()) for name in ('ipcreg01', 'ipcreg02')]
    io = json.loads((results/'manifestio01/summary.json').read_text())
    assert all(r['passed'] for r in micro+[io])
    slow = json.loads((results/'ipcreg01/profiles/worker6_large_base_registered.json').read_text())
    read = next(r for r in slow['rows'] if r['stage'] == 'pickle_read')
    imports = [r for r in slow['rows'] if r['stage'] == 'ipc_import']
    started, gcs = {}, []
    for e in slow['gc_events']:
        key = e['thread'], e['generation']
        if e['phase'] == 'start': started[key] = e['time_ns']
        elif key in started:
            begin = started.pop(key)
            gcs.append((e['time_ns']-begin)/1e9)
    payload = (results/'ipcreg01/large_base_registered.pkl').read_bytes()
    globals_used = [op.name for op, arg, pos in pickletools.genops(payload)
                    if op.name in ('GLOBAL', 'STACK_GLOBAL', 'REDUCE', 'BUILD', 'PERSID', 'BINPERSID')]
    result = {'development': 'v0.1.23.3', 'task': 'task2', 'diagnostic_validation_passed': True,
        'original_3_5s_root_cause_confirmed': False,
        'original_event': {'run': 'baselocation03', 'variant': 'local_base', 'gpu': 4, 'tp': 0,
                           'mapping_s': 3.502940622, 'fine_grained_trace_available': False},
        'instrumented_replay': {'gpu_pair': [6, 7], 'container': 'fresh isolated image; original container stopped',
            'scope': 'hardware changed due occupancy; no identical-environment historical reproduction claim',
            'rank_mapping': [{'variant': r['variant'], 'tp': r['tp'], 'mapping_s': r['mapping_window_s'],
                'ipc_s': r['stages']['ipc_import']['wall_s'], 'ipc_cpu_s': r['stages']['ipc_import']['cpu_s']}
                for r in profiles['reports']],
            'load_samples': [{'variant': r['variant'], 'load_s': r['timing']['load_s'],
                'verified_bytes': r['verification']['checked_bytes'], 'generation_checks': len(r['replies'])} for r in full['restores']]},
        'controlled_probes': micro,
        'captured_stall': {'path': 'HbmTarget.__init__ / pickle_read', 'manifest_bytes': len(payload),
            'total_mapping_s': micro[0]['samples'][1]['mapping_s'], 'file_loading_stage': read,
            'ipc_total_s': sum(r['wall_s'] for r in imports), 'ipc_max_s': max(r['wall_s'] for r in imports),
            'max_gc_s': max(gcs, default=0), 'pickle_global_or_reduce_opcodes': globals_used,
            'interpretation': 'intermittent blocking inside file open/read/unpickle/close stage; low thread CPU and short GC exclude decoding work or GC as dominant in this sample',
            'unresolved': 'this captured sample predates per-read tracing; exact syscall and lower-layer cause are unproven'},
        'backing_store_probe': {k: io[k] for k in ('passed', 'payload_bytes', 'registered_bytes')},
        'backing_store_samples': [{k: r[k] for k in ('storage', 'state', 'read_s', 'cpu_s')} for r in io['samples']],
        'ruled_out_as_deterministic_explanation': ['registered base always causes seconds of IPC import: second controlled pass remained fast',
            'normal metadata decoding or steady storage import necessarily costs seconds',
            'GC dominates the captured 1.28-second file-loading stall'],
        'next_step': 'retain opt-in per-open/read/close tracing; evaluate host-local memory-backed handoff with matched full-load A/B before promoting a fix',
        'performance_fix_claimed': False,
        'implementation': ['opt-in target preparation trace with wall/CPU/fault/context-switch counters and GC events',
            'per-storage IPC/tensor-binding timing; per-file-operation timing added after the captured stall',
            'trace files flush after target_mapped; instrumentation remains off by default'],
        'limitations': ['synthetic IPC probes validate sampled sentinel bytes, not full model content',
            'two controlled passes only; no tail-latency distribution or tmpfs speedup established',
            'the diagnostic reader adds overhead; instrumented main-metric samples are not final benchmark results']}
    out.mkdir(parents=True, exist_ok=False)
    (out/'diagnosis.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({'original_root_cause_confirmed': False, 'captured_stall_s': read['wall_s'],
                      'captured_cpu_s': read['thread_cpu_s'], 'max_gc_s': max(gcs, default=0),
                      'pickle_globals': globals_used, 'validation_passed': True}, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--out', type=Path, required=True)
    main(p.parse_args().out)
