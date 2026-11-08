"""Exercise limited-memory placement and real SSD extent round trips."""
import multiprocessing
import socket
import time
import urllib.request

from fluidckpt.cluster.eviction import plan_eviction
from fluidckpt.serving.ssd_store import serve


def test_eviction_preserves_coverage_when_dram_cannot_hold_model():
    size = 1 << 20
    sources = [{'tp': r, 'identity': f'model/{r}', 'n': 5*size+17} for r in range(2)]
    locations = [{'tp': r, 'worker': r, 'tier': tier, 'node': node, 'capacity_bytes': cap}
                 for r in range(2) for tier, node, cap in
                 [('dram', 'n0', size), ('dram', 'n1', 2*size), ('ssd', 'n2', 3*size)]]
    scenarios = [{'node': n, 'ssd_GBps': .5, 'local_GBps': 35, 'remote_GBps': 20} for n in ['n0', 'n1']]
    plan = plan_eviction(sources, locations, scenarios, block_bytes=size)
    for source in sources:
        cursor = 0
        for extent in [e for e in plan['extents'] if e['tp'] == source['tp']]:
            assert extent['off'] == cursor
            cursor += extent['n']
        assert cursor == source['n']
    assert sum(e['n'] for e in plan['extents'] if e['tier'] == 'ssd') >= 4*size+34


def test_remote_ssd_offset_write_commit_read(tmp_path):
    import torch
    from fluidckpt.memory.ssd import RemoteExtent
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    process = multiprocessing.Process(target=serve, args=(str(tmp_path), port))
    process.start()
    endpoint = f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(endpoint+'/status', timeout=.2).close(); break
            except OSError: time.sleep(.02)
        source = torch.arange(65553, dtype=torch.int64).remainder(251).to(torch.uint8)
        extent = RemoteExtent(endpoint, 'rank0/tail', source.numel())
        extent.write(0, source[:32768]); extent.write(32768, source[32768:])
        extent.commit()
        assert torch.equal(extent.read(17, source.numel()-17), source[17:])
        extent.close()
        assert not list(tmp_path.glob('*.bin'))
    finally:
        process.terminate(); process.join(5)
