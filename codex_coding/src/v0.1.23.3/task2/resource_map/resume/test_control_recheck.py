"""CPU-only checks for the exact-owner read-only classification boundary."""
import unittest
from unittest.mock import patch
import control_recheck as control


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.identity = dict(boot_id="boot", container_id="container", container_started_at="start")
        self.expected = dict(pid=123, start_time="456", command=["python3", "owned.py"])
        self.actual = dict(pid=123, exists=True, state="S", live=True, start_time="456",
                           pgid=123, argv=["python3", "owned.py"])

    def test_matching_identity(self):
        self.assertTrue(control.continuity(self.identity, self.identity)["passed"])

    def test_each_lifecycle_key_is_required(self):
        for key in self.identity:
            changed = dict(self.identity, **{key: "new"})
            self.assertFalse(control.continuity(self.identity, changed)["passed"])
            missing = dict(self.identity)
            missing.pop(key)
            self.assertFalse(control.continuity(self.identity, missing)["passed"])

    def test_identity_change_blocks_absent_or_live(self):
        self.assertEqual(control.classify(self.expected, self.actual, False), "identity_continuity_blocked")
        self.assertEqual(control.classify(self.expected, {"exists": False}, False), "identity_continuity_blocked")

    def test_absent_and_z_are_exit_evidence(self):
        self.assertEqual(control.classify(self.expected, {"exists": False}, True), "exit_evidence_absent")
        self.assertEqual(control.classify(self.expected, dict(self.actual, state="Z"), True), "exit_evidence_zombie")

    def test_reused_pid_is_not_exit_evidence(self):
        self.assertEqual(control.classify(self.expected, dict(self.actual, state="Z", start_time="999"), True),
                         "pid_reused_blocked")

    def test_pgid_and_argv_mismatch_block(self):
        for updates in (dict(pgid=999), dict(argv=["other.py"])):
            self.assertEqual(control.classify(self.expected, dict(self.actual, **updates), True), "owner_mismatch_blocked")

    def test_exact_live_is_only_eligible(self):
        self.assertEqual(control.classify(self.expected, self.actual, True), "exact_live_cleanup_eligible")

    def test_local_task_preserves_exact_old_records(self):
        task = control.task_spec()
        own = [r for r in task["nodes"]["node29"]["owners"] if r["kind"] == "old_probe"]
        self.assertEqual(sorted((r["pid"], r["start_time"]) for r in own), [(21883, "475875814"), (21901, "475875846")])
        self.assertEqual(len([r for r in task["nodes"]["node27"]["owners"] if r["kind"] == "protected"]), 3)

    def test_changed_old_state_hash_rejected(self):
        original = control.sha
        path = control.RUN / "rank_2.process.pid.json"
        with patch.object(control, "sha", side_effect=lambda value: "changed" if control.Path(value) == path else original(value)):
            with self.assertRaisesRegex(ValueError, "fixed spec/state SHA"):
                control.task_spec()

    def test_changed_original_boot_rejected(self):
        changed = dict(control.OLD_IDENTITIES)
        changed["node29"] = ("other_boot", *changed["node29"][1:])
        with patch.object(control, "OLD_IDENTITIES", changed):
            with self.assertRaisesRegex(ValueError, "lifecycle identity"):
                control.task_spec()


if __name__ == "__main__":
    unittest.main()
