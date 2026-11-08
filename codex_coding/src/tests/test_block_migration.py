"""Check mixed-location recall and idempotent continuation across block commits."""
import copy
from fluidckpt.cluster.block_migration import migrate_blocks


class ExactByteCluster:
    def __init__(self):
        self.replicas = {f'source/{i}': dict(id=f'source/{i}', identity='model-v1', tp=0,
            off=i*4, n=4, worker=0, tier='dram', data=bytes(range(i*4, i*4+4))) for i in range(3)}
        self.transfers = 0

    def inventory(self, compact):
        return [], list(self.replicas.values())

    def plan_restore(self, targets, strategy, source_ids):
        return targets[0], source_ids[0]

    def transfer(self, plan, verify_before_ready):
        target, source_id = plan
        assert verify_before_ready
        self.replicas[target['id']] = dict(target, data=self.replicas[source_id]['data'])
        self.transfers += 1

    def retire(self, source_id):
        source = self.replicas[source_id]
        assert any(row['id'] != source_id and row['off'] == source['off'] and row['data'] == source['data']
                   for row in self.replicas.values())
        del self.replicas[source_id]

    def recall(self):
        blocks = {row['off']: row['data'] for row in self.replicas.values()}
        return b''.join(blocks[off] for off in sorted(blocks))


def test_recall_between_commits_and_resume_without_recopy(tmp_path):
    cluster = ExactByteCluster()
    actions = []
    for source in list(cluster.replicas.values()):
        target = copy.deepcopy(source)
        del target['data']
        target.update(id=source['id'].replace('source', 'destination'), worker=1, mode='replica')
        actions.append({'source_id': source['id'], 'target': target})
    iterator = migrate_blocks(cluster, actions, tmp_path/'journal.json')
    first = next(iterator)
    assert first['transferred_bytes_this_attempt'] == 4
    assert cluster.recall() == bytes(range(12))
    assert {r['worker'] for r in cluster.replicas.values()} == {0, 1}
    iterator.close()
    continued = list(migrate_blocks(cluster, actions, tmp_path/'journal.json'))
    assert continued[0]['transferred_bytes_this_attempt'] == 0
    assert cluster.transfers == 3
    assert cluster.recall() == bytes(range(12))
