"""A shared multi-host handoff must never import another host's HIP IPC handle."""
import os
import pickle
from types import SimpleNamespace

from fluidckpt.serving.keeper import Worker


def test_same_gpu_number_on_two_hosts_selects_only_local_rank(tmp_path):
    folder = tmp_path/'tp16'
    folder.mkdir()
    host = os.uname().nodename
    for rank, gpu, owner, phase in [(0, 0, host, 'capture'),
                                    (8, 0, host+'-remote', 'capture'),
                                    (1, 1, host, 'capture'),
                                    (2, 0, host, 'restore')]:
        meta = {'tp_rank': rank, 'gpu_id': gpu, 'host': owner, 'phase': phase}
        (folder/f'rank{rank}.pkl').write_bytes(pickle.dumps({'meta': meta}))
    worker = Worker.__new__(Worker)
    worker.a = SimpleNamespace(handoff=str(tmp_path))
    worker.gpu = 0
    assert [meta['tp_rank'] for _, meta in worker._my_pkls('tp16', 'capture')] == [0]
    assert [meta['tp_rank'] for _, meta in worker._my_pkls('tp16', 'restore')] == [2]
