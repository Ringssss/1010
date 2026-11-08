"""Tickets remove planning from the load window, not physical prepare validation."""
import copy
from unittest.mock import patch
import pytest
from test_managed import make_case, MemoryCluster


def setup():
    ws, q = make_case()
    c = MemoryCluster(ws)
    p = c.plan_joint(q); c.apply_joint(p)
    return c, p, q['targets']


def test_ticket_is_one_shot_and_skips_inventory_without_skipping_transfer():
    c, p, targets = setup()
    ticket = c.preplan_managed_restore(p, targets)
    expected = copy.deepcopy(c._restore_tickets[ticket['token']]['plan'])
    targets[0]['n'] = 1
    with patch.object(c, 'inventory', side_effect=AssertionError('inventory on hot path')), patch.object(c, 'transfer', return_value='sent') as transfer:
        assert c.transfer_preplanned_restore(ticket['token'], verify_before_ready=False, retain=True) == 'sent'
        transfer.assert_called_once_with(expected, verify_before_ready=False, retain=True)
    with pytest.raises(ValueError, match='consumed'):
        c.transfer_preplanned_restore(ticket['token'])


def test_ticket_profile_change_and_transfer_failure_cannot_be_replayed():
    c, p, targets = setup()
    token = c.preplan_managed_restore(p, targets)['token']
    c.profile['provenance'] = 'new'
    with pytest.raises(ValueError, match='profile'):
        c.transfer_preplanned_restore(token)
    c, p, targets = setup()
    token = c.preplan_managed_restore(p, targets)['token']
    with patch.object(c, 'transfer', side_effect=ValueError('worker epoch changed')):
        with pytest.raises(ValueError, match='epoch'):
            c.transfer_preplanned_restore(token)
    with pytest.raises(ValueError, match='consumed'):
        c.transfer_preplanned_restore(token)
