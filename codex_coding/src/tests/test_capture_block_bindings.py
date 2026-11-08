"""Capture can use aggregate NUMA space even when a full shard fits nowhere."""
from fluidckpt.memory.bindings import choose_block_bindings


def test_shards_split_across_numa_capacity_without_overbooking():
    page = 4096
    topology = {'nodes': [
        {'node': 0, 'distance': '10 20', 'memory_bytes': {'MemFree': 3*page}},
        {'node': 1, 'distance': '20 10', 'memory_bytes': {'MemFree': 5*page}}],
        'gpus': [{'logical_gpu': 0, 'numa_node': 0}, {'logical_gpu': 1, 'numa_node': 1}]}
    result = choose_block_bindings(topology, [{'gpu': 0, 'nbytes': 4*page},
                                             {'gpu': 1, 'nbytes': 4*page}], block_bytes=page, guard_bytes=0)
    assert result['remaining_after_guard_bytes'] == {0: 0, 1: 0}
    assert [b['numa'] for b in result['blocks'][0]] == [0, 0, 0, 1]
    assert [b['numa'] for b in result['blocks'][1]] == [1, 1, 1, 1]
