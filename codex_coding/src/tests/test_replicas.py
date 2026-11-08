"""Replica generation, partial coverage and lifetime invariants without device dependencies."""
import pytest
from fluidckpt.serving.replicas import ReplicaCatalog


class Buffer:
    def __init__(self, n): self.n = n
    def numel(self): return self.n


class Owner:
    allocated_bytes = 32
    closed = False
    def close(self): self.closed = True


def operation(**changes):
    return dict({"replica_id": "r", "generation": 1, "identity": "m", "tp": 0,
                 "dst_w": 0, "off": 105, "source_offset": 5, "n": 12}, **changes)


def catalog():
    c = ReplicaCatalog(extra_budget=64)
    c.publish("r", "m", 0, 100, Buffer(32), 0, 0)
    return c


@pytest.mark.parametrize("change", [{"identity": "other"}, {"tp": 1}, {"dst_w": 1},
                                    {"off": 99}, {"source_offset": 105}, {"n": 99},
                                    {"generation": 2}, {"n": 1.0}])
def test_invalid_source_rejected(change):
    c = catalog()
    with pytest.raises(ValueError), c.acquire([operation(**change)]):
        pass
    assert c.inventory()[0]["leases"] == 0


def test_retirement_waits_for_lease_and_generation_changes():
    c = ReplicaCatalog(extra_budget=64)
    owner = Owner()
    c.publish("r", "m", 0, 100, Buffer(32), 0, 0, owner)
    with c.acquire([operation()]):
        assert c.retire("r")["state"] == "RETIRING"
        assert not owner.closed
        with pytest.raises(ValueError), c.acquire([operation()]): pass
    assert owner.closed and not c.inventory() and c.owned_bytes() == 0
    new = c.publish("r", "m", 0, 100, Buffer(32), 0, 0)
    assert new["generation"] == 2
    with pytest.raises(ValueError), c.acquire([operation()]): pass


def test_primary_release_denied_while_in_use():
    c = catalog()
    with c.acquire([operation()]):
        with pytest.raises(RuntimeError): c.retire_identity("m")
    c.retire_identity("m")
    assert not c.inventory()


def test_acquisition_is_atomic():
    c = catalog()
    with pytest.raises(ValueError), c.acquire([operation(), operation(replica_id="absent")]): pass
    assert c.inventory()[0]["leases"] == 0


def test_extra_budget_and_duplicate_ids():
    c = ReplicaCatalog(extra_budget=16)
    with pytest.raises(MemoryError): c.publish("r", "m", 0, 0, Buffer(32), 0, 0, Owner())
    c = catalog()
    with pytest.raises(ValueError): c.publish("r", "m", 0, 0, Buffer(32), 0, 0)
