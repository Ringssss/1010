"""Worker-owned allocations with explicit publication and transaction-scoped leases."""
import copy


class Catalog:
    def __init__(self, budgets):
        self.budgets = dict(budgets)
        self.entries = {}
        self.transactions = {}

    def used(self):
        return {tier: sum(e["allocated_bytes"] for e in self.entries.values()
                          if e["tier"] == tier and e["state"] != "RELEASED") for tier in self.budgets}

    def inventory(self, compact=False):
        excluded = {"tensor", "owner"} | ({"meta", "index"} if compact else set())
        return [copy.deepcopy({k: v for k, v in e.items() if k not in excluded})
                for e in self.entries.values()]

    def reserve(self, desc, allocate):
        previous = self.entries.get(desc["id"])
        if previous and previous["state"] != "RELEASED":
            raise ValueError("replica already exists")
        if desc["tier"] not in self.budgets or type(desc["n"]) is not int or desc["n"] <= 0:
            raise ValueError("invalid allocation request")
        n = (desc["n"]+4095)//4096*4096
        if self.used()[desc["tier"]]+n > self.budgets[desc["tier"]]:
            raise MemoryError("replica capacity budget exceeded")
        tensor, owner = allocate()
        e = dict(desc, generation=previous["generation"]+1 if previous else 1,
                 state="BUILDING", leases=0, allocated_bytes=n, tensor=tensor, owner=owner)
        self.entries[e["id"]] = e
        return e

    def publish(self, rid, verified):
        e = self.entries[rid]
        if e["state"] != "BUILDING" or not verified:
            raise ValueError("only verified BUILDING replicas can be published")
        if hasattr(e['owner'], 'publish'):
            e['owner'].publish()
        e["state"] = "VALID"

    def acquire(self, tx, ops):
        if tx in self.transactions:
            raise ValueError("transaction already exists")
        selected = {}
        for op in ops:
            e = self.entries.get(op["replica_id"])
            if e is None or e["state"] != "VALID" or e["generation"] != op["generation"]:
                raise ValueError("missing or stale source")
            if e["identity"] != op["identity"] or e["tp"] != op["tp"] or e["worker"] != op["src_w"]:
                raise ValueError("source model, rank or owner mismatch")
            off, n = op["source_offset"], op["n"]
            if (type(off) is not int or type(n) is not int or off < 0 or n <= 0
                    or off+n > e["n"] or op["off"] != off+e["off"]):
                raise ValueError("source extent mismatch")
            selected[e["id"]] = e
        for e in selected.values():
            e["leases"] += 1
        self.transactions[tx] = selected

    def release(self, tx):
        for e in self.transactions.pop(tx, {}).values():
            e["leases"] -= 1
            if not e["leases"] and e["state"] == "RETIRING":
                self._close(e)

    def retire(self, rid):
        e = self.entries[rid]
        if e["state"] == "RELEASED": return
        e["state"] = "RETIRING"
        if not e["leases"]: self._close(e)

    def _close(self, e):
        e["tensor"] = None
        if e["owner"] is not None:
            e["owner"].close()
            e["owner"] = None
        e["state"] = "RELEASED"
