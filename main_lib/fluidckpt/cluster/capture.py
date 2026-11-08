"""Capture immutable weights into independently releasable pinned allocations."""
import json
import os
import pickle
import time


def capture_regions(worker, tag, snap, block_bytes=256 << 20, timeout=7200, numa_by_rank=None,
                    publish_marker=True, verify_full_sha=False):
    import torch
    from fluidckpt.serving.keeper import HbmTarget
    from fluidckpt.serving.replicas import identity

    if type(block_bytes) is not int or block_bytes <= 0 or block_bytes % 4096:
        raise ValueError('capture block size must be positive and page aligned')
    if type(verify_full_sha) is not bool:
        raise ValueError('capture full-SHA flag must be boolean')
    deadline = time.monotonic()+timeout
    exports = worker._my_pkls(tag, 'capture')
    while not exports and time.monotonic() < deadline:
        time.sleep(.1)
        exports = worker._my_pkls(tag, 'capture')
    if not exports:
        raise TimeoutError(f'no capture export for {tag} on {worker.node}:{worker.gpu}')
    torch.cuda.set_device(worker.gpu)
    results = []
    for path, meta in exports:
        started = time.monotonic()
        with open(path, 'rb') as stream:
            data = pickle.load(stream)
        index, total = {}, 0
        for entry in data['entries']:
            index[entry['name']] = (total, entry['nbytes'])
            total += entry['nbytes']
        model_identity = identity(meta, index)
        ids = [f'{snap}/rank{meta["tp_rank"]}/block{offset//block_bytes}'
               for offset in range(0, total, block_bytes)]
        numas = ((numa_by_rank or {}).get(str(meta['tp_rank']))
                 if numa_by_rank is not None else [worker.a.numa]*len(ids))
        if numas is None or len(numas) != len(ids):
            raise ValueError('NUMA binding list must cover every capture block')
        if any(rid in worker.catalog.entries and worker.catalog.entries[rid]['state'] != 'RELEASED' for rid in ids):
            raise ValueError('capture regions already exist')
        source = HbmTarget(path, index, worker.gpu)
        created = []
        verification = []
        try:
            for rid, offset, numa in zip(ids, range(0, total, block_bytes), numas):
                size = min(block_bytes, total-offset)
                desc = {'id': rid, 'identity': model_identity, 'tp': meta['tp_rank'],
                        'off': offset, 'n': size, 'tier': 'dram', 'worker': worker.rank,
                        'node': worker.node, 'gpu': worker.gpu, 'numa': numa,
                        'meta': meta, 'index': index, 'epoch': worker.epoch}
                entry = worker.catalog.reserve(desc, lambda: worker.allocate(desc))
                created.append(rid)
                copied = source.gather(offset, entry['tensor'])
                torch.cuda.synchronize()
                if copied != size:
                    raise RuntimeError('capture interval was not fully covered')
                if verify_full_sha:
                    source_sha = worker.extent_digest(entry, 0, size)
                    engine_sha = worker.target_digest(source, offset, size)
                    if source_sha != engine_sha:
                        raise RuntimeError('capture full-SHA mismatch: ' + rid)
                    verification.append({'id': rid, 'off': offset, 'n': size,
                                         'source_sha256': source_sha, 'engine_sha256': engine_sha})
                worker.catalog.publish(rid, True)
        except BaseException:
            torch.cuda.synchronize()
            for rid in created:
                worker.catalog.retire(rid)
            raise
        finally:
            del source
        # Return one manifest per rank; replica rows do not repeat the large tensor index.
        result = {'manifest': {'identity': model_identity, 'tp': meta['tp_rank'], 'n': total,
                               'meta': meta, 'index': index},
                  'regions': [{k: v for k, v in worker.catalog.entries[rid].items()
                               if k not in ('tensor', 'owner', 'meta', 'index')} for rid in ids],
                  'capture_s': time.monotonic()-started, 'block_bytes': block_bytes,
                  'full_sha_enabled': verify_full_sha,
                  'capture_s_scope': 'metadata, IPC mapping, allocation and D2H' + (' plus full SHA' if verify_full_sha else ''),
                  'publication': ('completed D2H copy and full SHA before VALID publication' if verify_full_sha
                                  else 'completed D2H copy; full hash verification is a separate operation')}
        if verify_full_sha:
            result['verification'] = {'passed': True, 'checked_bytes': sum(row['n'] for row in verification),
                                      'rows': verification, 'position': 'bootstrap outside online timer',
                                      'mapping': 'same single IPC import used for capture and verification'}
        if publish_marker:
            marker = os.path.join(os.path.dirname(path), f'rank{meta["tp_rank"]}.captured')
            with open(marker+'.tmp', 'w') as stream:
                json.dump({'regions': len(ids), 'bytes': total, 'capture_s': result['capture_s']}, stream)
            os.replace(marker+'.tmp', marker)
        worker.log(ev='capture_regions', tp=meta['tp_rank'], nbytes=total, regions=len(ids),
                   elapsed_s=result['capture_s'])
        results.append(result)
    return results
