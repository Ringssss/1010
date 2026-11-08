"""Apply approved placement plans with resumable checkpoints under one coordinator."""
import copy
import json
from pathlib import Path
import time
import uuid

from .managed import (capacity_limits, check_snapshot, digest, fits, normalize_request, rows, semantic,
                      solve_joint_request, state_hash, usage, verify_plan_hash, scenario_rows)
from .path_catalog import check_profile_evidence
from .feedback import update_feedback


def rpc(*args, **kwargs):
    from .control import request
    return request(*args, **kwargs)


class ManagedPlacementError(RuntimeError):
    def __init__(self, message, journal):
        super().__init__(message)
        self.journal = copy.deepcopy(journal)


def persist(journal, path):
    if path is not None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix+".tmp")
        temporary.write_text(json.dumps(journal, indent=2, allow_nan=False))
        temporary.replace(target)


class ManagedPlacementMixin:
    def preplan_managed_restore(self, placement, targets):
        """Compile before load_begin; prepare still checks epochs and acquires leases."""
        with self.lock:
            started = time.monotonic()
            plan = self.plan_managed_restore(placement, targets)
            token = uuid.uuid4().hex
            if not hasattr(self, '_restore_tickets'):
                self._restore_tickets = {}
            self._restore_tickets[token] = {'plan': copy.deepcopy(plan),
                'profile_hash': digest(self.profile), 'executor': self.executor}
            return {'token': token, 'planning_s': time.monotonic()-started,
                    'placement_plan_id': placement['plan_id'],
                    'scope': 'one-shot in-memory plan; physical source checks deferred to transactional prepare'}

    def transfer_preplanned_restore(self, token, verify_before_ready=True, retain=False):
        with self.lock:
            if self.active:
                raise ValueError('active restore prevents ticket consumption')
            record = getattr(self, '_restore_tickets', {}).pop(token, None)
            if record is None:
                raise ValueError('unknown or consumed restore ticket')
            if record['profile_hash'] != digest(self.profile) or record['executor'] != self.executor:
                raise ValueError('profile or executor changed; preplan again')
            return self.transfer(record['plan'], verify_before_ready=verify_before_ready, retain=retain)

    def discard_preplanned_restore(self, token):
        with self.lock:
            if token not in getattr(self, '_restore_tickets', {}):
                raise ValueError('unknown or consumed restore ticket')
            self._restore_tickets.pop(token)

    def plan_joint(self, request):
        with self.lock:
            if self.active: raise ValueError("active restore prevents placement planning")
            workers, _ = self.inventory(compact=True)
            normalized = normalize_request(request, workers)
            if normalized["executor"] != self.executor: raise ValueError("placement executor does not match runtime")
            if self.planner_endpoint:
                plan = rpc(self.planner_endpoint, "plan_joint", {"workers": workers, "request": normalized, "profile": self.profile})
            else:
                plan = solve_joint_request(workers, normalized, self.profile)
            verify_plan_hash(plan)
            if plan["initial_state"] != state_hash(workers) or plan["request"] != normalized:
                raise ValueError("planner changed request or snapshot")
            if plan["profile_hash"] != digest(self.profile): raise ValueError("planner used a different cost profile")
            return plan

    def apply_joint(self, plan, journal=None, journal_path=None, fail_after_actions=None):
        with self.lock:
            verify_plan_hash(plan)
            if self.active: raise ValueError("active restore prevents layout changes")
            if plan["profile_hash"] != digest(self.profile): raise ValueError("cost profile changed; replan placement")
            q = plan["request"]; actions = plan["transition"]["actions"]
            if q["executor"] != self.executor: raise ValueError("executor changed after placement planning")
            if journal is None and journal_path is not None and Path(journal_path).exists():
                journal = json.loads(Path(journal_path).read_text())
            if journal is None:
                journal = self.placement_journals.get(plan["plan_id"])
            journal = copy.deepcopy(journal) if journal is not None else {
                "schema_version": 1, "plan_id": plan["plan_id"], "next_action": 0,
                "expected_state": plan["initial_state"], "completed": [], "pending": None, "status": "new"}
            if (journal.get("schema_version") != 1 or journal.get("plan_id") != plan["plan_id"]
                    or type(journal.get("next_action")) is not int or not 0 <= journal["next_action"] <= len(actions)):
                raise ValueError("journal belongs to a different or invalid plan")
            if len(journal.get("completed", [])) != journal["next_action"]:
                raise ValueError("journal completed-action count mismatch")
            for i, done in enumerate(journal["completed"]):
                if done["index"] != i or done["after_state"] != actions[i]["after_state"]:
                    raise ValueError("journal action chain mismatch")
            self.placement_journals[plan["plan_id"]] = journal
            def observe():
                ws, _ = self.inventory(compact=True); check_snapshot(ws)
                check_profile_evidence(ws, self.profile)
                return ws
            def acknowledge(action, result=None, recovered=False):
                journal["completed"].append({"index": journal["next_action"], "kind": action["kind"],
                    "replica_id": action["replica"]["id"], "after_state": action["after_state"],
                    "recovered_after_publication": recovered, "result": result})
                journal["next_action"] += 1
                journal.update(expected_state=action["after_state"], pending=None)
                persist(journal, journal_path)
            try:
                ws = observe(); actual = state_hash(ws)
                index = journal["next_action"]
                if journal["pending"] is not None:
                    if index >= len(actions) or journal["pending"] != index: raise ValueError("invalid pending checkpoint")
                    action = actions[index]
                    if actual == action["after_state"]:
                        acknowledge(action, recovered=True)
                    elif actual == action["before_state"]:
                        journal["pending"] = None
                    else: raise ValueError("uncertain action state; explicit replan required")
                if state_hash(ws) != journal["expected_state"]: raise ValueError("stale placement snapshot or worker generation")
                expected = plan["initial_state"] if journal["next_action"] == 0 else actions[journal["next_action"]-1]["after_state"]
                if journal["expected_state"] != expected: raise ValueError("journal checkpoint state mismatch")
                journal.update(status="applying", error=None)
                persist(journal, journal_path)
                for index in range(journal["next_action"], len(actions)):
                    action = actions[index]
                    ws = observe()
                    if state_hash(ws) != action["before_state"]: raise ValueError("snapshot changed before action")
                    # Re-evaluate physical headroom using the currently held managed allocations.
                    current_ids = (set(q["managed_ids"]) | {a["replica"]["id"] for a in actions})
                    current_ids &= {r["id"] for r in rows(ws, True)}
                    current_q = dict(q, managed_ids=sorted(current_ids))
                    fresh_limits = capacity_limits(current_q, ws)
                    if not fits(action["usage"], fresh_limits, peak=True): raise ValueError("physical headroom changed before action")
                    journal["pending"] = index; persist(journal, journal_path)
                    stamp = time.monotonic()
                    r = action["replica"]
                    if action["kind"] == "retire":
                        self.retire(r["id"])
                        result = {"wall_s": time.monotonic()-stamp}
                    else:
                        full_workers, replicas = self.inventory()
                        if state_hash(full_workers) != action["before_state"]: raise ValueError("snapshot changed before build")
                        source = next(x for x in replicas if x["id"] in q["base_ids"]
                                      and x["identity"] == r["identity"] and x["tp"] == r["tp"])
                        target = dict(r, key=r["id"], mode="replica", meta=source["meta"], index=source["index"])
                        flow = self.transfer(self.plan_restore([target], "resource_lp", q["base_ids"]))
                        if not flow["verification"]["passed"]: raise RuntimeError("built replica failed content verification")
                        result = {"wall_s": time.monotonic()-stamp, "verified_bytes": flow["verification"]["checked_bytes"],
                                  "execute_s": flow["execute_wall_s"], "strategy": flow["plan"]["strategy"],
                                  "fallback_reason": flow["plan"]["fallback_reason"]}
                    ws = observe()
                    if state_hash(ws) != action["after_state"]: raise ValueError("actual catalog differs from committed action")
                    acknowledge(action, result)
                    if fail_after_actions is not None and journal["next_action"] >= fail_after_actions:
                        raise RuntimeError("injected interruption after checkpointed action")
                ws = observe()
                if state_hash(ws) != plan["transition"]["final_state"]: raise ValueError("final catalog differs from plan")
                selected_ids = {r["id"] for r in plan["selected"]}
                held = [r for r in rows(ws, True) if r["id"] in selected_ids]
                final_q = dict(q, managed_ids=sorted(selected_ids))
                if not fits(usage(held, ws), capacity_limits(final_q, ws)): raise ValueError("final budget audit failed")
                journal.update(status="applied", final_usage=usage(held, ws), pending=None, error=None)
                persist(journal, journal_path)
                return copy.deepcopy(journal)
            except Exception as exc:
                journal.update(status="interrupted", error=repr(exc))
                persist(journal, journal_path)
                raise ManagedPlacementError(str(exc), journal) from exc

    def replan_joint(self, previous):
        """Re-snapshot after an aborted allocation or other explicitly acknowledged drift.

        Released tombstones advance generations and cannot be retried using the
        old state hashes. Preserve ownership scope, rebuild a valid request from
        published survivors, and let the caller review/apply the new plan.
        """
        with self.lock:
            verify_plan_hash(previous)
            if self.active: raise ValueError("active restore prevents replanning")
            workers, replicas = self.inventory(compact=True); check_snapshot(workers)
            expected = {a["replica"]["id"]: a["replica"] for a in previous["transition"]["actions"]}
            expected.update({r["id"]: r for r in previous["selected"]})
            survivors = [r for r in replicas if r["id"] in expected]
            if any(semantic(r) != semantic(expected[r["id"]]) for r in survivors):
                raise ValueError("owned replica identity or extent changed; refusing to expand ownership")
            q = dict(copy.deepcopy(previous["request"]), managed_ids=sorted(r["id"] for r in survivors))
            return self.plan_joint(q)

    def plan_managed_restore(self, placement, targets, scenario=None):
        with self.lock:
            verify_plan_hash(placement)
            record = self.placement_journals.get(placement["plan_id"])
            if not record or record["status"] != "applied": raise ValueError("placement has not been applied")
            if placement["profile_hash"] != digest(self.profile): raise ValueError("profile changed after placement")
            if placement["request"]["executor"] != self.executor: raise ValueError("executor changed after placement")
            keys = ("identity", "tp", "worker", "off", "n")
            signature = lambda ts: sorted(tuple(t.get(k, 0) for k in keys) for t in ts)
            matches = [s for s in scenario_rows(placement['request']) if
                       (scenario is None or s['name'] == scenario) and signature(targets) == signature(s['targets'])]
            if not matches or any(t.get('tier', 'hbm') != 'hbm' for t in targets):
                raise ValueError('restore demand differs from placement scenario')
            workers, _ = self.inventory(compact=True); check_snapshot(workers)
            if state_hash(workers) != placement["transition"]["final_state"]:
                raise ValueError("published layout changed; replan placement")
            sources = placement["protected_source_ids"]+[r["id"] for r in placement["selected"]]
            result = self.plan_restore(targets, "resource_lp", sources)
            result["placement_plan_id"] = placement["plan_id"]
            result['scenario'] = matches[0]['name']
            if 'service_model' in placement['request']:
                from .service_cost import estimate_execution, new_state
                result['service_prediction'] = estimate_execution(result, workers,
                    placement['request']['service_model'], new_state(workers))
            return result

    def record_path_feedback(self, observation, alpha=.25):
        with self.lock:
            if self.active:
                raise ValueError('active restore prevents profile changes')
            workers, _ = self.inventory(compact=True)
            check_snapshot(workers); check_profile_evidence(workers, self.profile)
            updated = update_feedback(self.profile, observation, alpha)
            before = digest(self.profile)
            self.profile = updated
            return {'before_profile_hash': before, 'after_profile_hash': digest(updated),
                    'revision': updated['feedback_revision'], 'sample': updated['feedback_history'][-1],
                    'requires_replan': True}
