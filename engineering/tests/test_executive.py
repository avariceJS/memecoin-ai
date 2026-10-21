"""Durable governance tests: no models, jobs, data or network involved."""

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from engineering.executive import Executive, ExecutiveError, StaleExecutive
from engineering.org_state import Store


class ExecutiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "org.sqlite"
        self.now = 1000.0
        self.store = Store(self.path, clock=lambda: self.now)
        self.executive = Executive(self.store)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def lease(self, owner="episode-1", **kwargs):
        return self.executive.claim(owner, **kwargs)

    def request(self, lease, request_id="R1", **kwargs):
        return self.executive.request(
            lease,
            request_id,
            "resource",
            "Platform",
            "Additional offline CPU budget",
            "Queue is measured",
            "Continue existing bounded tasks",
            **kwargs,
        )

    def test_model_and_reasoning_identity_are_checked_without_starting_model(self):
        for wrong in ({"model": "gpt-6.1-sol"}, {"reasoning": "low"}, {"reasoning": "High"}):
            with self.assertRaises(ExecutiveError):
                self.lease(**wrong)
        self.assertEqual(self.store.events(), [])
        lease = self.lease()
        self.assertEqual((lease["model"], lease["reasoning"]), ("gpt-6-astra", "high"))

    def test_only_one_live_episode_across_connections(self):
        other = Store(self.path, clock=lambda: self.now)
        try:
            alternate = Executive(other)
            with ThreadPoolExecutor(2) as pool:
                leases = list(
                    pool.map(
                        lambda pair: pair[0].claim(pair[1]),
                        [(self.executive, "one"), (alternate, "two")],
                    )
                )
            self.assertEqual(sum(lease is not None for lease in leases), 1)
            self.assertEqual(len(self.store.events()), 1)
        finally:
            other.close()

    def test_expiry_replacement_reconstructs_obligations_and_rejects_stale_episode(self):
        first = self.lease(lease_seconds=10)
        self.executive.obligation(
            first,
            "O1",
            "Preserve evidence",
            rationale="Recovery",
            evidence_refs=["receipt:synthetic"],
        )
        self.request(first)
        self.now += 10
        self.store.close()
        self.store = Store(self.path, clock=lambda: self.now)
        self.executive = Executive(self.store)
        second = self.lease("replacement")
        self.assertEqual(second["epoch"], first["epoch"] + 1)
        self.assertEqual(self.executive.snapshot()["obligations"][0]["id"], "O1")
        self.assertEqual(self.executive.snapshot()["requests"][0]["id"], "R1")
        for mutation in (
            lambda: self.executive.decision(first, "D1", "Stale"),
            lambda: self.executive.transition_obligation(
                first, "O1", "completed", completion_evidence=["fake"]
            ),
            lambda: self.executive.resolve_request(first, "R1", "resolved", "late"),
            lambda: self.executive.renew(first),
            lambda: self.executive.release(first),
        ):
            with self.assertRaises(StaleExecutive):
                mutation()
        self.executive.decision(second, "D2", "Continue existing obligation")

    def test_exact_deadline_rejects_even_before_replacement(self):
        first = self.lease(lease_seconds=1)
        self.now += 1
        with self.assertRaises(StaleExecutive):
            self.executive.decision(first, "D1", "Too late")
        self.assertEqual(self.executive.snapshot()["role"]["status"], "expired")

    def test_release_is_episodic_and_old_fence_cannot_reactivate(self):
        first = self.lease()
        self.executive.release(first, "Episode complete; teams continue")
        self.assertEqual(self.executive.snapshot()["role"]["status"], "released")
        with self.assertRaises(StaleExecutive):
            self.executive.renew(first)
        second = self.lease("episode-2")
        self.assertEqual(second["epoch"], 2)

    def test_renewal_retains_fence_and_records_latest_event(self):
        first = self.lease(lease_seconds=10)
        self.now += 5
        renewed = self.executive.renew(first, lease_seconds=20)
        self.assertEqual(renewed["epoch"], first["epoch"])
        self.assertEqual(renewed["lease_until"], 1025)
        self.executive.decision(first, "D1", "Original fence still identifies this episode")
        self.assertGreater(
            self.executive.snapshot()["role"]["last_event_seq"], renewed["last_event_seq"]
        )

    def test_decisions_immutable_idempotent_and_explicit_supersession(self):
        lease = self.lease()
        first = self.executive.decision(lease, "D1", "Initial scope", evidence_refs=["synthetic:1"])
        count = len(self.store.events())
        self.assertEqual(
            first,
            self.executive.decision(lease, "D1", "Initial scope", evidence_refs=["synthetic:1"]),
        )
        self.assertEqual(len(self.store.events()), count)
        with self.assertRaises(ExecutiveError):
            self.executive.decision(lease, "D1", "Silent overwrite")
        second = self.executive.decision(lease, "D2", "Revised scope", supersedes="D1")
        self.assertEqual(second["supersedes"], "D1")
        self.assertEqual(self.executive.get_decision("D1")["summary"], "Initial scope")
        with self.assertRaises(ExecutiveError):
            self.executive.decision(lease, "D3", "Unknown reference", supersedes="missing")

    def test_duplicate_request_after_replacement_is_idempotent(self):
        first = self.lease()
        request = self.request(first)
        self.executive.release(first)
        second = self.lease("replacement")
        count = len(self.store.events())
        self.assertEqual(self.request(second), request)
        self.assertEqual(len(self.store.events()), count)
        self.assertEqual(self.executive.snapshot()["counts"]["requests"], 1)

    def test_obligation_transitions_are_journaled_and_terminal_cannot_reopen(self):
        lease = self.lease()
        initial = self.executive.obligation(
            lease, "O1", "Review contract", owner_role="lead", team="Platform", blocked_by=["R1"]
        )
        self.assertEqual(initial["status"], "blocked")
        self.executive.transition_obligation(lease, "O1", "open")
        done = self.executive.transition_obligation(
            lease, "O1", "completed", completion_evidence=["receipt:approved"]
        )
        self.assertGreater(done["updated_seq"], initial["created_seq"])
        with self.assertRaises(ExecutiveError):
            self.executive.transition_obligation(lease, "O1", "open")
        self.assertEqual(self.executive.snapshot()["obligations"], [])
        self.assertEqual(
            self.executive.get_obligation("O1")["completion_evidence"], ["receipt:approved"]
        )
        transitions = [
            e for e in self.store.events() if e["kind"] == "executive_obligations_transition"
        ]
        self.assertEqual([e["body"]["to"] for e in transitions], ["open", "completed"])
        self.executive.obligation(lease, "O2", "Follow-up review")

    def test_generic_obligations_cannot_claim_dependency_reconciler_ids(self):
        lease = self.lease()
        before_events = len(self.store.events())
        before_role = self.executive.snapshot()["role"]

        with self.assertRaisesRegex(ExecutiveError, "prefix is reserved"):
            self.executive.obligation(lease, "blocked-dependencies/manual", "Generic duty")

        self.assertIsNone(self.executive.get_obligation("blocked-dependencies/manual"))
        self.assertEqual(len(self.store.events()), before_events)
        self.assertEqual(self.executive.snapshot()["role"], before_role)
        adjacent = self.executive.obligation(
            lease, "blocked-dependencies0/manual", "Adjacent generic duty"
        )
        self.assertEqual(adjacent["status"], "open")

    def test_completion_and_blockers_require_evidence(self):
        lease = self.lease()
        self.executive.obligation(lease, "O1", "Review")
        for status in ("completed", "blocked"):
            with self.assertRaises(ExecutiveError):
                self.executive.transition_obligation(lease, "O1", status)
        self.assertEqual(self.executive.get_obligation("O1")["status"], "open")

    def test_cancellation_requires_evidence_and_is_terminal(self):
        lease = self.lease()
        self.executive.obligation(lease, "O1", "Resolve dependency")
        with self.assertRaisesRegex(ExecutiveError, "cancellation requires evidence"):
            self.executive.transition_obligation(lease, "O1", "cancelled")
        cancelled = self.executive.transition_obligation(
            lease,
            "O1",
            "cancelled",
            cancellation_evidence=["dependency-readiness:sha256"],
        )
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["cancellation_evidence"], ["dependency-readiness:sha256"])
        with self.assertRaisesRegex(ExecutiveError, "terminal records cannot reopen"):
            self.executive.transition_obligation(lease, "O1", "open")

    def test_pending_resource_request_and_ceo_expiration_do_not_block_tasks(self):
        self.store.add_task("T1", "Platform", "Independent task")
        lease = self.lease(lease_seconds=1)
        self.request(lease)
        self.now += 2
        self.assertEqual([task["id"] for task in self.store.ready_tasks()], ["T1"])
        attempt = self.store.claim("T1", "worker")
        self.assertIsNotNone(attempt)
        self.assertEqual(self.executive.snapshot()["requests"][0]["status"], "open")

    def test_request_resolution_records_disposition_without_policy_or_task_changes(self):
        lease = self.lease()
        self.request(lease)
        self.store.put_contract("boundary", 1, {"paid_execution": False})
        resolved = self.executive.resolve_request(
            lease,
            "R1",
            "resolved",
            "Human response recorded",
            evidence_refs=["user:message-reference"],
        )
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(
            self.executive.get_request("R1")["resolution_evidence"], ["user:message-reference"]
        )
        self.assertEqual(self.store.get_contract("boundary")["body"], {"paid_execution": False})
        self.assertEqual(self.store.list_tasks(), [])
        self.assertEqual(self.executive.snapshot()["requests"], [])
        with self.assertRaises(ExecutiveError):
            self.executive.resolve_request(lease, "R1", "declined", "Rewrite history")

    def test_snapshot_and_russian_report_are_bounded_and_keep_detailed_history(self):
        lease = self.lease()
        for index in range(30):
            self.executive.decision(lease, f"D{index}", "Синтетическое решение " + str(index))
        self.request(lease)
        view = self.executive.snapshot(history_limit=3, open_limit=1)
        self.assertEqual(len(view["decisions"]), 3)
        self.assertEqual(len(view["events"]), 3)
        self.assertEqual(view["counts"]["decisions"], 30)
        self.assertEqual(self.executive.get_decision("D0")["summary"], "Синтетическое решение 0")
        self.assertLessEqual(len(self.executive.render_report(max_chars=200)), 200)
        self.assertIn("Ожидающие запросы", self.executive.render_report())
        self.assertEqual(len(self.executive.snapshot(history_limit=0)["events"]), 0)
        with self.assertRaises(ExecutiveError):
            self.executive.snapshot(history_limit=21)

    def test_founder_snapshot_uses_bounded_active_task_projection(self):
        for index in range(105):
            self.store.add_task(f"TASK-{index:03d}", "Engineering", f"Task {index}")
        view = self.executive.snapshot()
        self.assertEqual(len(view["tasks"]), 100)
        self.assertEqual(view["counts"]["tasks"], 105)
        self.assertEqual(view["counts"]["active_tasks"], 105)
        self.assertEqual(view["counts"]["omitted_active_tasks"], 5)
        self.assertEqual(view["counts"]["task_states"], {"pending": 105})
        self.assertEqual(len(self.store.list_tasks()), 105)
        report = self.executive.render_report()
        self.assertIn("pending: 105", report)
        self.assertIn("100 из 105", report)

    def test_journal_and_projection_rollback_together(self):
        lease = self.lease()
        before = self.store.events()
        original = self.executive._journal

        def fail_after_journal(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("simulated crash boundary")

        with patch.object(self.executive, "_journal", side_effect=fail_after_journal):
            with self.assertRaises(RuntimeError):
                self.executive.decision(lease, "D1", "Must rollback")
        self.assertIsNone(self.executive.get_decision("D1"))
        self.assertEqual(self.store.events(), before)
        self.executive.decision(lease, "D1", "Can retry after rollback")

    def test_department_lease_is_exclusive_fenced_and_survives_owner_replacement(self):
        first = self.executive.claim_department("Senses", "sol-episode-1", lease_seconds=10)
        self.assertEqual(first["epoch"], 1)
        self.assertIsNone(self.executive.claim_department("Senses", "sol-episode-2"))
        self.now += 10
        second = self.executive.claim_department("Senses", "sol-episode-2")
        self.assertEqual(second["epoch"], 2)
        self.assertEqual(self.executive.get_department_lease("Senses"), second)
        self.store.add_objective("offline", "Offline engineering")
        with self.assertRaises(StaleExecutive):
            self.executive.renew_department(first)
        with self.assertRaises(StaleExecutive):
            self.executive.release_department(first)
        with self.assertRaises(StaleExecutive):
            self.executive.record_department_plan(
                first,
                1,
                "offline",
                "Stale owner cannot replace a plan",
                ["Preserve the current fence"],
                self.now + 100,
                evidence_refs=["test:synthetic"],
            )
        self.executive.release_department(second, "Planning episode complete")
        self.assertEqual(self.executive.get_department_lease("Senses")["status"], "released")

    def test_department_leases_preserve_the_two_team_concurrency_envelope(self):
        one = self.executive.claim_department("Experience", "sol-experience")
        two = self.executive.claim_department("Platform", "sol-platform")
        self.assertIsNotNone(one)
        self.assertIsNotNone(two)
        self.assertIsNone(self.executive.claim_department("Evaluation", "sol-evaluation"))
        self.executive.release_department(one)
        self.assertIsNotNone(self.executive.claim_department("Evaluation", "sol-evaluation"))

    def test_department_plan_revisions_are_immutable_owned_and_visible(self):
        self.store.add_objective("offline", "Offline engineering")
        lease = self.executive.claim_department("Platform", "sol-platform-1")
        first = self.executive.record_department_plan(
            lease,
            1,
            "offline",
            "Close task admission gaps",
            ["Resolve frozen verifier binding", "Preserve task history"],
            self.now + 100,
            rationale="Current code has bounded admission but no complete execution binding.",
            evidence_refs=["commit:synthetic"],
        )
        self.assertEqual(first["owner"], "sol-platform-1")
        self.assertEqual(first["owner_epoch"], 1)
        self.assertEqual(first["status"], "current")
        self.assertEqual(
            self.executive.record_department_plan(
                lease,
                1,
                "offline",
                "Close task admission gaps",
                ["Resolve frozen verifier binding", "Preserve task history"],
                self.now + 100,
                rationale="Current code has bounded admission but no complete execution binding.",
                evidence_refs=["commit:synthetic"],
            ),
            first,
        )
        with self.assertRaisesRegex(ExecutiveError, "revision is immutable"):
            self.executive.record_department_plan(
                lease,
                1,
                "offline",
                "Different plan",
                ["Resolve frozen verifier binding"],
                self.now + 100,
                evidence_refs=["commit:synthetic"],
            )
        second = self.executive.record_department_plan(
            lease,
            2,
            "offline",
            "Verify merged source safely",
            ["Run pinned engineering gate"],
            self.now + 200,
            evidence_refs=["gate:synthetic"],
        )
        self.assertEqual(second["revision"], 2)
        self.assertEqual(self.executive.get_department_plan("Platform", 1)["status"], "superseded")
        self.assertEqual(self.executive.get_department_plan("Platform")["revision"], 2)
        self.now += 201
        view = self.executive.snapshot()
        self.assertEqual(view["schema"], "brain-executive-snapshot-v4")
        self.assertEqual(view["counts"]["department_plans"], 1)
        self.assertEqual(view["counts"]["department_plan_reviews_due"], 1)
        self.assertEqual(view["department_plans"][0]["review_state"], "due")
        self.assertIn(
            "Планы команд: 1; срок пересмотра наступил: 1.", self.executive.render_report()
        )

    def test_department_conflicts_are_plan_bound_fenced_and_superseded(self):
        self.store.add_objective("offline", "Offline engineering")
        platform = self.executive.claim_department("Platform", "sol-platform")
        experience = self.executive.claim_department("Experience", "sol-experience")
        for lease, summary, evidence in (
            (platform, "Pin verifier admission", "commit:platform-v1"),
            (experience, "Preserve schema compatibility", "commit:experience-v1"),
        ):
            self.executive.record_department_plan(
                lease,
                1,
                "offline",
                summary,
                ["Resolve the shared contract boundary"],
                self.now + 100,
                evidence_refs=[evidence],
            )

        first = self.executive.escalate_department_conflict(
            platform,
            "C-1",
            "Experience",
            "The contract change requires incompatible release order.",
            own_plan_revision=1,
            other_plan_revision=1,
            rationale="Both teams identify conflicting immutable contract constraints.",
            evidence_refs=["plan:Platform/1", "plan:Experience/1"],
        )
        self.assertEqual(
            (first["status"], first["team_a"], first["team_b"]), ("open", "Experience", "Platform")
        )
        self.assertEqual(
            self.executive.escalate_department_conflict(
                platform,
                "C-1",
                "Experience",
                "The contract change requires incompatible release order.",
                own_plan_revision=1,
                other_plan_revision=1,
                rationale="Both teams identify conflicting immutable contract constraints.",
                evidence_refs=["plan:Platform/1", "plan:Experience/1"],
            ),
            first,
        )

        ceo = self.lease("ceo-episode")
        resolved = self.executive.adjudicate_department_conflict(
            ceo,
            "C-1",
            1,
            "select_team_a",
            "The Experience plan preserves the existing consumer contract.",
            ["review:contract-compatibility"],
        )
        self.assertEqual(resolved["adjudicator_epoch"], ceo["epoch"])
        self.assertEqual(self.executive.get_department_conflict("C-1")["status"], "resolved")
        with self.assertRaisesRegex(ExecutiveError, "changed plan revisions"):
            self.executive.escalate_department_conflict(
                platform,
                "C-UNCHANGED",
                "Experience",
                "Attempt to reopen the resolved conflict without new plans.",
                own_plan_revision=1,
                other_plan_revision=1,
                evidence_refs=["plan:Platform/1", "plan:Experience/1"],
                supersedes="C-1",
            )
        self.assertEqual(
            self.executive.adjudicate_department_conflict(
                ceo,
                "C-1",
                1,
                "select_team_a",
                "The Experience plan preserves the existing consumer contract.",
                ["review:contract-compatibility"],
            ),
            resolved,
        )
        with self.assertRaisesRegex(ExecutiveError, "immutable"):
            self.executive.adjudicate_department_conflict(
                ceo,
                "C-1",
                1,
                "select_team_b",
                "Changed decision",
                ["review:changed"],
            )

        deferred = self.executive.adjudicate_department_conflict(
            ceo,
            "C-1",
            2,
            "defer",
            "Wait for both owners to reconcile the plan pins.",
            ["review:defer"],
            supersedes_revision=1,
        )
        self.assertEqual(deferred["supersedes_revision"], 1)
        self.assertEqual(self.executive.get_department_conflict("C-1")["status"], "open")

        self.executive.record_department_plan(
            platform,
            2,
            "offline",
            "Reconcile verifier and schema pins",
            ["Publish a compatible shared contract"],
            self.now + 200,
            evidence_refs=["plan:platform-v2"],
        )
        with self.assertRaisesRegex(ExecutiveError, "plan revisions are stale"):
            self.executive.adjudicate_department_conflict(
                ceo,
                "C-1",
                3,
                "select_team_b",
                "The old plan pair no longer matches.",
                ["review:stale"],
                supersedes_revision=2,
            )
        stale_view = self.executive.snapshot(open_limit=10)
        self.assertEqual(stale_view["counts"]["department_conflicts_open"], 0)
        self.assertEqual(stale_view["counts"]["department_conflicts_stale"], 1)
        self.assertEqual(
            next(row for row in stale_view["department_conflicts"] if row["id"] == "C-1")["status"],
            "stale",
        )
        second = self.executive.escalate_department_conflict(
            experience,
            "C-2",
            "Platform",
            "The revised Platform plan needs a fresh decision.",
            own_plan_revision=1,
            other_plan_revision=2,
            rationale="The earlier source plan has been superseded.",
            evidence_refs=["plan:Experience/1", "plan:Platform/2"],
            supersedes="C-1",
        )
        self.assertEqual(second["status"], "open")
        self.assertEqual(self.executive.get_department_conflict("C-1")["status"], "superseded")
        self.executive.adjudicate_department_conflict(
            ceo,
            "C-2",
            1,
            "select_team_a",
            "The Experience plan remains compatible with the revised pin.",
            ["review:fresh"],
        )
        view = self.executive.snapshot(open_limit=1)
        self.assertEqual(view["schema"], "brain-executive-snapshot-v4")
        self.assertEqual(view["counts"]["department_conflicts"], 2)
        self.assertEqual(view["counts"]["department_conflicts_open"], 0)
        self.assertEqual(view["counts"]["department_conflicts_stale"], 0)
        self.assertEqual(len(view["department_conflicts"]), 1)
        self.assertEqual(view["department_conflicts"][0]["id"], "C-2")
        self.assertIn("Открытые конфликты отделов: 0 из 2.", self.executive.render_report())

    def test_contract_review_lane_is_independent_of_consumer_task_readiness(self):
        self.store.put_contract("sample", 1, {"schema": "v1"})
        producer = "EXP-1"
        self.store.add_task(producer, "Experience", "Produce v2", scope=["schema.py"])
        attempt = self.store.claim(producer, "worker", 60)
        self.store.submit(attempt["id"], attempt["epoch"], "candidate", {})
        self.store.verify(producer, "candidate", True, {"passed": True})
        self.store.accept(producer, "candidate")
        self.store.integrate(producer, "candidate", "integration", {"merged": True})

        transition = self.store.propose_contract_transition(
            "sample",
            1,
            2,
            {"schema": "v2"},
            producer_task_id=producer,
            producer_team="Experience",
            consumer_teams=["Empty", "Research"],
        )
        digest = transition["body"]["contract_hash"]
        self.store.acknowledge_contract_transition("sample", 2, "Experience", digest)
        self.store.add_task(
            "research-v2",
            "Research",
            "Requires pending v2",
            scope=["research.py"],
            contracts={"sample": 2},
        )
        self.store.add_task("unrelated", "Platform", "Still ready", scope=["other.py"])

        self.assertIsNone(self.store.claim("research-v2", "worker", 60))
        self.assertEqual([task["id"] for task in self.store.ready_tasks()], ["unrelated"])
        self.assertIsNone(
            self.store.get_decision(self.store._contract_ack_key("sample", 2, "Research"))
        )
        first = self.executive.snapshot(open_limit=10)
        second = self.executive.snapshot(open_limit=10)
        self.assertEqual(first["schema"], "brain-executive-snapshot-v4")
        self.assertEqual(first["contract_review_items"], second["contract_review_items"])
        self.assertEqual(first["counts"]["contract_reviews_pending"], 2)
        self.assertEqual(first["counts"]["contract_reviews_omitted"], 0)
        self.assertEqual(
            [item["participant_team"] for item in first["contract_review_items"]],
            ["Empty", "Research"],
        )
        store_projection = self.store.contract_transition_review_projection(limit=1)
        self.assertEqual(len(store_projection["items"]), 1)
        self.assertEqual(store_projection["pending_count"], 2)
        self.assertEqual(store_projection["omitted_count"], 1)
        research = first["contract_review_items"][1]
        self.assertEqual(research["participant_role"], "consumer")
        self.assertEqual(research["contract_hash"], digest)
        self.assertEqual(
            self.store.get_contract_transition("sample", 2)["body"]["body"],
            {"schema": "v2"},
        )
        self.assertEqual(self.store.get_contract("sample")["version"], 1)
        bounded = self.executive.snapshot(open_limit=1)
        self.assertEqual(len(bounded["contract_review_items"]), 1)
        self.assertEqual(bounded["counts"]["contract_reviews_pending"], 2)
        self.assertEqual(bounded["counts"]["contract_reviews_omitted"], 1)
        self.assertIn("только показывает недостающие отзывы", self.executive.render_report())
        self.store.acknowledge_contract_transition("sample", 2, "Research", digest)
        self.store.acknowledge_contract_transition("sample", 2, "Empty", digest)
        self.assertEqual(
            self.store.contract_transition_review_projection(limit=0),
            {"items": [], "pending_count": 0, "omitted_count": 0},
        )
        self.assertTrue(self.store.activate_contract_transition("sample", 2)["created"])
        activated = self.executive.snapshot(open_limit=10)
        self.assertEqual(activated["counts"]["contract_reviews_pending"], 0)
        self.assertEqual(activated["contract_review_items"], [])

    def test_conflict_adjudication_rejects_stale_ceo_fence_and_conflict_reuse(self):
        self.store.add_objective("offline", "Offline engineering")
        platform = self.executive.claim_department("Platform", "sol-platform")
        experience = self.executive.claim_department("Experience", "sol-experience")
        for team_lease in (platform, experience):
            team = team_lease["team"]
            self.executive.record_department_plan(
                team_lease,
                1,
                "offline",
                "Resolve shared boundary",
                ["Preserve current contract"],
                self.now + 100,
                evidence_refs=["plan:" + team],
            )
        self.executive.escalate_department_conflict(
            platform,
            "C-STALE",
            "Experience",
            "Conflicting contract plans.",
            own_plan_revision=1,
            other_plan_revision=1,
            evidence_refs=["plan:Platform/1", "plan:Experience/1"],
        )
        ceo = self.lease("ceo-old")
        self.executive.release(ceo, "Episode complete")
        replacement = self.lease("ceo-new")
        with self.assertRaises(StaleExecutive):
            self.executive.adjudicate_department_conflict(
                ceo,
                "C-STALE",
                1,
                "defer",
                "Old episode cannot decide.",
                ["review:stale-epoch"],
            )
        with self.assertRaisesRegex(ExecutiveError, "ID reused"):
            self.executive.escalate_department_conflict(
                platform,
                "C-STALE",
                "Experience",
                "Different conflicting plans.",
                own_plan_revision=1,
                other_plan_revision=1,
                evidence_refs=["plan:Platform/1", "plan:Experience/1"],
            )
        self.executive.adjudicate_department_conflict(
            replacement,
            "C-STALE",
            1,
            "defer",
            "Current episode records this unresolved conflict.",
            ["review:current-epoch"],
        )

    def test_conflict_adjudication_snapshots_ceo_token_before_lock_wait(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from concurrent.futures import TimeoutError as FutureTimeout

        self.store.add_objective("offline", "Offline engineering")
        platform = self.executive.claim_department("Platform", "sol-platform")
        experience = self.executive.claim_department("Experience", "sol-experience")
        for team_lease in (platform, experience):
            self.executive.record_department_plan(
                team_lease,
                1,
                "offline",
                "Maintain the shared contract boundary",
                ["Keep proposal revisions explicit"],
                self.now + 100,
                evidence_refs=["plan:" + team_lease["team"]],
            )
        self.executive.escalate_department_conflict(
            platform,
            "C-LOCK",
            "Experience",
            "Record an immutable source-plan conflict.",
            own_plan_revision=1,
            other_plan_revision=1,
            evidence_refs=["plan:Platform/1", "plan:Experience/1"],
        )
        ceo = self.lease("ceo-lock")
        blocker = Store(self.path, clock=lambda: self.now)
        waiting_for_write_lock = threading.Event()
        self.store.db.set_trace_callback(
            lambda statement: (
                waiting_for_write_lock.set()
                if statement.strip().upper() == "BEGIN IMMEDIATE"
                else None
            )
        )
        blocker.db.execute("BEGIN IMMEDIATE")
        try:
            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(
                    self.executive.adjudicate_department_conflict,
                    ceo,
                    "C-LOCK",
                    1,
                    "defer",
                    "Keep the exact decision token captured before waiting.",
                    ["review:lock-wait"],
                )
                try:
                    self.assertTrue(waiting_for_write_lock.wait(2))
                    with self.assertRaises(FutureTimeout):
                        future.result(timeout=0.05)
                    ceo["owner"] = "mutated-during-lock-wait"
                    blocker.db.execute("COMMIT")
                    result = future.result(timeout=2)
                    self.assertEqual(result["adjudicator"], "ceo-lock")
                finally:
                    if blocker.db.in_transaction:
                        blocker.db.execute("ROLLBACK")
                    if not future.done():
                        future.result(timeout=2)
        finally:
            self.store.db.set_trace_callback(None)
            if blocker.db.in_transaction:
                blocker.db.execute("ROLLBACK")
            blocker.close()

    def test_department_plan_reader_uses_one_snapshot_across_connections(self):
        self.store.add_objective("offline", "Offline engineering")
        lease = self.executive.claim_department("Platform", "sol-platform")
        first = {
            "lease": lease,
            "revision": 1,
            "objective_id": "offline",
            "summary": "Record first plan",
            "priorities": ["Preserve coherent reads"],
            "review_at": self.now + 100,
            "evidence_refs": ["commit:first"],
        }
        self.executive.record_department_plan(**first)

        other_store = Store(self.path, clock=lambda: self.now)
        try:
            other = Executive(other_store)
            original_record = self.executive._department_plan_record
            inserted = False

            def insert_next_revision(*args):
                nonlocal inserted
                if not inserted:
                    inserted = True
                    other.record_department_plan(
                        lease,
                        2,
                        "offline",
                        "Record second plan",
                        ["Keep snapshot classification stable"],
                        self.now + 200,
                        evidence_refs=["commit:second"],
                    )
                return original_record(*args)

            with patch.object(
                self.executive, "_department_plan_record", side_effect=insert_next_revision
            ):
                observed = self.executive.get_department_plan("Platform", 1)

            self.assertEqual(observed["status"], "current")
            self.assertEqual(other.get_department_plan("Platform")["revision"], 2)
        finally:
            other_store.close()

    def test_department_plan_rejects_bad_dates_revisions_and_authority_expansion(self):
        self.store.add_objective("offline", "Offline engineering")
        lease = self.executive.claim_department("Research", "sol-research-1")
        kwargs = {
            "lease": lease,
            "revision": 1,
            "objective_id": "offline",
            "summary": "Plan a synthetic bounded check",
            "priorities": ["Inspect synthetic fixture"],
            "review_at": self.now + 100,
            "evidence_refs": ["fixture:synthetic"],
        }
        for values in (
            {"review_at": True},
            {"review_at": float("inf")},
            {"review_at": self.now},
            {"revision": True},
            {"priorities": []},
            {"priorities": ["same", "same"]},
            {"priorities": ["x" * 301]},
            {"evidence_refs": []},
            {"objective_id": "missing"},
        ):
            with self.subTest(values=values), self.assertRaises(ExecutiveError):
                self.executive.record_department_plan(**{**kwargs, **values})
        before = self.store.events()
        with self.assertRaises(ExecutiveError):
            self.executive.claim_department("Research", "worker", model="gpt-6-luna")
        self.assertEqual(self.store.events(), before)
        self.assertEqual(self.executive.get_department_plan("Research"), None)

    def test_department_plan_duplicate_race_is_idempotent_across_connections(self):
        from concurrent.futures import ThreadPoolExecutor

        self.store.add_objective("offline", "Offline engineering")
        lease = self.executive.claim_department("Evaluation", "sol-eval-1")
        other_store = Store(self.path, clock=lambda: self.now)
        other_executive = Executive(other_store)
        plan = {
            "lease": lease,
            "revision": 1,
            "objective_id": "offline",
            "summary": "Track synthetic validation gaps",
            "priorities": ["Retain negative results"],
            "review_at": self.now + 100,
            "evidence_refs": ["protocol:synthetic"],
        }
        try:
            with ThreadPoolExecutor(2) as pool:
                results = list(
                    pool.map(
                        lambda executive: executive.record_department_plan(**plan),
                        (self.executive, other_executive),
                    )
                )
            self.assertEqual(results[0], results[1])
            count = self.store.db.execute(
                "SELECT COUNT(*) FROM executive_department_plans WHERE team='Evaluation'"
            ).fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            other_store.close()

    def test_department_projection_is_bounded_and_reports_full_counts(self):
        self.store.add_objective("offline", "Offline engineering")
        for team in ("Cortex", "Experience"):
            lease = self.executive.claim_department(team, "sol-" + team.lower())
            self.executive.record_department_plan(
                lease,
                1,
                "offline",
                "Maintain bounded " + team + " work",
                ["Keep current gates intact"],
                self.now + 100,
                evidence_refs=["status:synthetic"],
            )
        view = self.executive.snapshot(open_limit=1)
        self.assertEqual(view["counts"]["department_plans"], 2)
        self.assertEqual(view["counts"]["department_leases"], 2)
        self.assertEqual(view["counts"]["department_leases_active"], 2)
        self.assertEqual(len(view["department_plans"]), 1)
        self.assertEqual(len(view["department_leases"]), 1)

    def test_department_plan_snapshots_lease_before_sqlite_lock_wait(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from concurrent.futures import TimeoutError as FutureTimeout

        self.store.add_objective("offline", "Offline engineering")
        lease = self.executive.claim_department("Cortex", "sol-cortex-1")
        priorities = ["Bind plan to the claimed department owner"]
        blocker = Store(self.path, clock=lambda: self.now)
        waiting_for_write_lock = threading.Event()
        self.store.db.set_trace_callback(
            lambda statement: (
                waiting_for_write_lock.set()
                if statement.strip().upper() == "BEGIN IMMEDIATE"
                else None
            )
        )
        blocker.db.execute("BEGIN IMMEDIATE")
        try:
            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(
                    self.executive.record_department_plan,
                    lease,
                    1,
                    "offline",
                    "Retain the lease input snapshot",
                    priorities,
                    self.now + 100,
                    evidence_refs=["test:synthetic"],
                )
                try:
                    self.assertTrue(waiting_for_write_lock.wait(2))
                    with self.assertRaises(FutureTimeout):
                        future.result(timeout=0.05)
                    lease["owner"] = "mutated-during-lock-wait"
                    priorities[0] = "replace plan after transaction began"
                    blocker.db.execute("COMMIT")
                    result = future.result(timeout=2)
                    self.assertEqual(result["owner"], "sol-cortex-1")
                    self.assertEqual(
                        result["priorities"], ["Bind plan to the claimed department owner"]
                    )
                finally:
                    if blocker.db.in_transaction:
                        blocker.db.execute("ROLLBACK")
                    if not future.done():
                        future.result(timeout=2)
        finally:
            self.store.db.set_trace_callback(None)
            if blocker.db.in_transaction:
                blocker.db.execute("ROLLBACK")
            blocker.close()


if __name__ == "__main__":
    unittest.main()
