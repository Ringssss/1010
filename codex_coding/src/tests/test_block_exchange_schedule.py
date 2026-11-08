"""Check an exchange that fits finally but cannot duplicate either complete model."""
import pytest
from fluidckpt.cluster.block_migration import schedule_block_moves


def exchange():
    replicas = []
    moves = []
    for worker, model in [(0, 'A'), (1, 'B')]:
        for block in range(5):
            row = dict(id=f'{model}/{block}', identity=model, tp=0, off=block*4096, n=4096,
                       worker=worker, tier='dram')
            replicas.append(row)
            moves.append({'source_id': row['id'], 'target': dict(row, id=row['id']+'/moved',
                                                                worker=1-worker, mode='replica')})
    return replicas, moves


def test_exchange_needs_only_one_block_of_temporary_space_per_host():
    replicas, moves = exchange()
    plan = schedule_block_moves(replicas, moves, {(0, 'dram'): 6*4096, (1, 'dram'): 6*4096})
    active = {row['id']: row for row in replicas}
    transferred = 0
    for action in plan['actions']:
        target = action['target']
        active[target['id']] = target
        # The source still exists while the new destination is being verified.
        for worker in [0, 1]:
            assert sum(row['n'] for row in active.values() if row['worker'] == worker) <= 6*4096
        del active[action['source_id']]
        for model in ['A', 'B']:
            assert sorted(row['off'] for row in active.values() if row['identity'] == model) == [i*4096 for i in range(5)]
        transferred += target['n']
    assert transferred == 10*4096
    assert {row['worker'] for row in active.values() if row['identity'] == 'A'} == {1}
    assert {row['worker'] for row in active.values() if row['identity'] == 'B'} == {0}


def test_no_spare_block_is_reported_before_any_mutation():
    replicas, moves = exchange()
    with pytest.raises(ValueError, match='no move fits'):
        schedule_block_moves(replicas, moves, {(0, 'dram'): 5*4096, (1, 'dram'): 5*4096})
    assert all('/moved' not in row['id'] for row in replicas)


def test_shared_host_budget_is_counted_once_for_each_physical_copy():
    replicas, moves = exchange()
    capacity = {(0, 'dram'): 6*4096, (1, 'dram'): 6*4096, 'all_hosts': 10*4096}
    keys = lambda row: [(row['worker'], row['tier']), 'all_hosts']
    with pytest.raises(ValueError, match='no move fits'):
        schedule_block_moves(replicas, moves, capacity, keys)
    capacity['all_hosts'] += 4096
    plan = schedule_block_moves(replicas, moves, capacity, keys)
    assert plan['peak_bytes']['all_hosts'] == 11*4096
    assert plan['final_bytes']['all_hosts'] == 10*4096
