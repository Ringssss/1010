"""Recover the same live memfd pages and leases after a CPU owner process exits."""
import json
import importlib
import multiprocessing
import os
from pathlib import Path
import time
import pytest

from fluidckpt.memory.owner import Mapping, Store, call, process_stamp, serve


@pytest.mark.skipif(not hasattr(os, 'memfd_create'), reason='requires Linux memfd support in Python')
def test_live_client_reconstitutes_owner_without_copying(tmp_path):
    socket_path = tmp_path/'o.sock'
    context = multiprocessing.get_context('fork')
    original = context.Process(target=serve, args=(str(socket_path), 1 << 20))
    replacement = None
    original.start()
    def wait_ready():
        deadline = time.monotonic()+10
        while True:
            try:
                return call(socket_path, 'inventory')[0]
            except (FileNotFoundError, ConnectionRefusedError):
                if time.monotonic() > deadline: raise
                time.sleep(.02)
    try:
        wait_ready()
        first = Mapping(socket_path, 'block', 4096, metadata={'id': 'block', 'identity': 'model', 'n': 4096})
        first.map[:4] = b'abcd'; first.publish()
        original.kill(); original.join(5)
        socket_path.unlink()
        reports = tmp_path/'reports'; reports.mkdir()
        (reports/'worker0.json').write_text(json.dumps({'pid': os.getpid(), 'stamp': process_stamp(os.getpid()),
            'manifests': {'model': {'meta': {}, 'index': {'bytes': [0, 4096]}}},
            'regions': [{'metadata': {'id': 'block', 'identity': 'model', 'n': 4096, 'numa': None},
                         'fd': first.fd, 'inode': os.fstat(first.fd).st_ino, 'lease': first.info['lease']}]}))
        replacement = context.Process(target=serve, args=(str(socket_path), 1 << 20, str(reports)))
        replacement.start()
        assert wait_ready()['leases'] == 1
        second = Mapping(socket_path, 'block')
        assert second.map[:4] == b'abcd'
        second.map[0] = 91
        assert first.map[0] == 91
        call(socket_path, 'retire', key='block')
        first.close()
        assert call(socket_path, 'inventory')[0]['used_bytes'] == 4096
        second.close()
        assert call(socket_path, 'inventory')[0]['used_bytes'] == 0
    finally:
        for process in [original, replacement]:
            if process is not None and process.is_alive(): process.terminate(); process.join(5)


def test_free_page_guard_rejects_before_creating_memfd(monkeypatch):
    monkeypatch.setattr(importlib.import_module('fluidckpt.topology.discover'),
                        'memory_node', lambda _: {'MemFree': 4096})
    def unexpected(*args, **kwargs): raise AssertionError('allocation must not start')
    monkeypatch.setattr(os, 'memfd_create', unexpected, raising=False)
    store = Store(1 << 20, free_page_guard_bytes=4096)
    with pytest.raises(MemoryError, match='free-page reserve'):
        store.dispatch({'operation': 'allocate', 'key': 'x', 'nbytes': 4096, 'numa': 0}, os.getpid())
