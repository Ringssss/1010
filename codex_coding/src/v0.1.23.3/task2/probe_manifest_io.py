"""Decompose manifest reads on disk versus tmpfs under a registered-base footprint."""
import argparse
import hashlib
import json
from pathlib import Path
import pickle
import resource
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT/'main_lib'))


def main(args):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    from fluidckpt.memory import HostAllocation
    from fluidckpt.serving.prepare_trace import PrepareTrace
    import torch
    torch.set_num_threads(1); torch.cuda.set_device(args.gpu)
    if torch.cuda.mem_get_info(args.gpu)[0] < 60 << 30:
        raise RuntimeError('selected GPU is occupied')
    args.out.mkdir(parents=True, exist_ok=False)
    payload = args.source.read_bytes()
    expected = pickle.loads(payload)
    n = sum(e['nbytes'] for e in expected['entries'])
    base = HostAllocation(n+(1 << 30), 'numa_registered', args.numa)
    tmpfs = Path('/dev/shm')/('fluidckpt-manifest-'+uuid.uuid4().hex+'.pkl')
    records = []
    try:
        for label, path in [('disk', args.out/'manifest.pkl'), ('tmpfs', tmpfs)]:
            stamp = time.monotonic(); path.write_bytes(payload); publish_s = time.monotonic()-stamp
            for state in ('first_read', 'second_read'):
                trace = PrepareTrace(args.out/'profiles', args.gpu, label+'_'+state)
                try:
                    with trace.span('pickle_read', path=str(path)):
                        with trace.span('file_open'): stream = open(path, 'rb')
                        try: value = pickle.load(trace.reader(stream))
                        finally:
                            with trace.span('file_close'): stream.close()
                finally: trace.finish()
                assert value == expected
                detail = json.loads((args.out/'profiles'/f'worker{args.gpu}_{label}_{state}.json').read_text())
                overall = next(r for r in detail['rows'] if r['stage'] == 'pickle_read')
                row = {'storage': label, 'state': state, 'publish_s': publish_s, 'read_s': overall['wall_s'],
                       'cpu_s': overall['thread_cpu_s'], 'details': detail['rows']}
                records.append(row); print(json.dumps({k: v for k, v in row.items() if k != 'details'}), flush=True)
        result = {'passed': True, 'payload_bytes': len(payload), 'payload_sha256': hashlib.sha256(payload).hexdigest(),
                  'registered_bytes': n+(1 << 30), 'samples': records,
                  'scope': 'same payload; no live IPC import or inference; two reads per backing store, not latency statistics'}
        (args.out/'summary.json').write_text(json.dumps(result, indent=2))
    finally:
        base.close(); tmpfs.unlink(missing_ok=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--source', required=True, type=Path)
    p.add_argument('--out', required=True, type=Path); p.add_argument('--gpu', required=True, type=int)
    p.add_argument('--numa', required=True, type=int)
    main(p.parse_args())
