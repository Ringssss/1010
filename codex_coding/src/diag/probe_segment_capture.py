"""Verify separate-process HIP capture into independently releasable DRAM blocks."""
import argparse
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import resource
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'main_lib'))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
SIZES = [9*(1 << 20)+17, 39*(1 << 20)]


def producer(handoff):
    import torch
    from fluidckpt.serving.sglang_hook import export_storages
    torch.set_num_threads(1)
    torch.cuda.set_device(0)
    os.environ.update(FLUIDCKPT_HANDOFF=handoff, FLUIDCKPT_TAG='fixture',
                      FLUIDCKPT_MODEL_ID='segment-capture-fixture', FLUIDCKPT_TP_SIZE='1')
    model = torch.nn.Module()
    offset = 0
    for number, n in enumerate(SIZES):
        value = (torch.arange(n, dtype=torch.int64)+offset).remainder(251).to(torch.uint8).cuda()
        model.register_parameter(f'weight{number}', torch.nn.Parameter(value, requires_grad=False))
        offset += n
    export_storages(model, 0, 0, 'capture')
    deadline = time.monotonic()+120
    while not (Path(handoff)/'fixture/rank0.captured').exists():
        if time.monotonic() > deadline:
            raise TimeoutError('capture marker did not appear')
        time.sleep(.05)


def rss():
    for line in Path('/proc/self/status').read_text().splitlines():
        if line.startswith('VmRSS:'):
            return int(line.split()[1])*1024


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--spread-numa', action='store_true')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    import torch
    from fluidckpt.cluster.worker import ClusterWorker
    from fluidckpt.cluster.capture import capture_regions
    torch.set_num_threads(1)
    torch.cuda.set_device(0)
    proc = multiprocessing.get_context('spawn').Process(target=producer, args=(str(args.out/'handoff'),))
    proc.start()
    worker = ClusterWorker(SimpleNamespace(node='node28', gpu=0, numa=2,
        handoff=str(args.out/'handoff'), session='segment-probe',
        dram_budget=64 << 20, hbm_budget=64 << 20, memory_backend='numa_registered'))
    worker.rank = 0
    report = {'scope': 'synthetic bytes, not GLM inference or a migration benchmark'}
    try:
        placement = {'0': [2, 1, 2, 1]} if args.spread_numa else None
        captures = capture_regions(worker, 'fixture', 'fixture', block_bytes=16 << 20, timeout=120,
                                   numa_by_rank=placement)
        proc.join(30)
        assert proc.exitcode == 0, proc.exitcode
        hashes = {}
        placement_checks = {}
        for row in captures[0]['regions']:
            expected = (torch.arange(row['n'], dtype=torch.int64)+row['off']).remainder(251).to(torch.uint8)
            actual = worker.catalog.entries[row['id']]['tensor']
            assert torch.equal(actual, expected)
            hashes[row['id']] = hashlib.sha256(actual.numpy()).hexdigest()
            placed = worker.catalog.entries[row['id']]['owner'].placement
            assert placed == {row['numa']: 1.0}
            placement_checks[row['id']] = placed
        del actual, expected
        first = captures[0]['regions'][0]['id']
        owner = worker.catalog.entries[first]['owner']
        before = rss()
        worker.catalog.retire(first)
        after = rss()
        assert owner.closed and owner.map is None
        for row in captures[0]['regions'][1:]:
            actual = worker.catalog.entries[row['id']]['tensor']
            assert hashlib.sha256(actual.numpy()).hexdigest() == hashes[row['id']]
        del actual
        report.update(status='passed', captured_bytes=sum(SIZES), independent_blocks=len(hashes),
                      producer_exited=True, first_allocation_closed=True, rss_before=before, rss_after=after,
                      catalog_bytes_after=worker.catalog.used(), hashes=hashes, placements=placement_checks)
        print(json.dumps(report), flush=True)
    finally:
        for rid in list(worker.catalog.entries):
            worker.catalog.retire(rid)
        if proc.is_alive():
            proc.terminate(); proc.join(10)
        (args.out/'result.json').write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
