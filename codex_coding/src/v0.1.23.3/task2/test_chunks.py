"""Chunk selection fails before RPC and keeps two non-overlapping pipeline slots."""
from unittest.mock import patch
import pytest
from fluidckpt.cluster.control import Cluster
from fluidckpt.executor.chunks import execution_chunk, DEFAULT_CHUNK, STAGING_BYTES


@pytest.mark.parametrize('size', [0, 17, True, 128 << 20])
def test_bad_chunk_rejected_before_rpc(size):
    c = Cluster(['unused'], {})
    with patch.object(c, 'inventory', side_effect=AssertionError('unexpected RPC')):
        with pytest.raises(ValueError, match='chunk'):
            c.transfer({'chunk_bytes': size})
    assert not c.active


def test_same_memory_budget_and_pipeline_slots():
    import torch
    staging = torch.empty(STAGING_BYTES, dtype=torch.uint8)
    a, b = staging[:DEFAULT_CHUNK], staging[DEFAULT_CHUNK:]
    assert a.numel()+b.numel() == STAGING_BYTES
    assert b.data_ptr()-a.data_ptr() == DEFAULT_CHUNK
    assert execution_chunk({}) == DEFAULT_CHUNK
    assert execution_chunk({'chunk_bytes': STAGING_BYTES}) == STAGING_BYTES
    with pytest.raises(ValueError, match='two 32'):
        execution_chunk({'executor': 'double_buffer', 'chunk_bytes': STAGING_BYTES})
