"""Retrospective native-slot count samples from verified PumpSwap reference facts."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from memecoin_experience import block_reconciliation, observations
from memecoin_experience.storage import (
    PIN,
    atomic_json,
    digest,
    lock,
    safe_path,
    sync_dir,
)

from . import slot_invocations

VERSION = "pumpswap-logical-slot-count-samples-v1"
MAX_HORIZONS = 16
MAX_SLOT_ROWS = 256
RECEIPTS = "pumpswap-slot-target-receipts"
INCOMPLETE_TRACE_COUNTERS = (
    "unsupported_traces",
    "malformed_instruction_data",
    "unresolved_role_maps",
    "protected_entity_rejections",
)

CONTRACT = dict(
    version=VERSION,
    coordinate_domain=slot_invocations.DOMAIN,
    coordinate_domain_pin=slot_invocations.DOMAIN_PIN,
    process_version=slot_invocations.PROCESS_VERSION,
    subject_entity_pin=digest(slot_invocations.PROGRAM_ENTITY),
    target_process="recognized_pumpswap_instruction_reference_count_v1",
    target_window="[origin_slot+1,origin_slot+horizon_slots+1)_v1",
    maturity="target_window_end_slot_exclusive_and_support_receipt_availability_v1",
    decision_availability="origin_slot_max_provider_capture_received_ns_v1",
    target_availability="exact_support_or_positive_lower_bound_evidence_received_ns_v1",
    forecast_semantics="retrospective_slot_replay_not_historical_pit_forecast",
    support_statuses=[
        "complete",
        "partial",
        "conflict",
        "skipped",
        "failed",
        "stopped",
        "unknown",
        "unsupported",
    ],
    target_statuses=["exact", "lower_bound", "masked", "pending"],
    role="exploratory_no_source_role",
    split_policy="no_independent_train_validation_test_from_two_slot_episode_v1",
)
CONTRACT_PIN = digest(CONTRACT)


def _pin_list(values):
    if not isinstance(values, list) or any(
        not isinstance(v, str) or not PIN.fullmatch(v) for v in values
    ):
        raise ValueError("pumpswap_slot_target_evidence")
    return values


def _validated_support_rows(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_SLOT_ROWS:
        raise ValueError("pumpswap_slot_target_support_rows")
    previous = None
    output = {}
    required = {
        "slot",
        "status",
        "available_ns",
        "disposition",
        "reason",
        "enumeration_relation",
        "enumeration_status",
        "transport_status",
        "lineage_status",
        "proof_available_ns",
        "proof_evidence",
        "evidence",
    }
    for row in rows:
        if not isinstance(row, dict) or not required <= set(row):
            raise ValueError("pumpswap_slot_target_support_row")
        slot = row["slot"]
        if type(slot) is not int or slot < 0 or previous is not None and slot <= previous:
            raise ValueError("pumpswap_slot_target_support_order")
        previous = slot
        if row["status"] not in set(CONTRACT["support_statuses"]):
            raise ValueError("pumpswap_slot_target_support_status")
        at = row["available_ns"]
        if at is not None and (type(at) is not int or at < 0):
            raise ValueError("pumpswap_slot_target_support_availability")
        proof_at = row["proof_available_ns"]
        if proof_at is not None and (type(proof_at) is not int or proof_at < 0):
            raise ValueError("pumpswap_slot_target_proof_availability")
        if not isinstance(row["reason"], str) or not isinstance(row["disposition"], str):
            raise ValueError("pumpswap_slot_target_support_reason")
        if not isinstance(row["enumeration_relation"], str) or not isinstance(
            row["enumeration_status"], str
        ):
            raise ValueError("pumpswap_slot_target_enumeration")
        if not isinstance(row["transport_status"], str) or not isinstance(
            row["lineage_status"], str
        ):
            raise ValueError("pumpswap_slot_target_support_transport")
        evidence = _pin_list(row["evidence"])
        proof_evidence = _pin_list(row["proof_evidence"])
        output[slot] = dict(row, evidence=evidence, proof_evidence=proof_evidence)
    return output


def build_logical_samples(
    *,
    interval_start_slot,
    interval_end_slot,
    support_rows,
    slot_accounting,
    fact_counts,
    fact_available_ns,
    reconciliation_pin,
    observation_source_pin,
    horizons=(1,),
):
    """Build bounded retrospective targets from per-slot support and verified positives."""
    if (
        type(interval_start_slot) is not int
        or type(interval_end_slot) is not int
        or interval_start_slot < 0
        or interval_end_slot <= interval_start_slot
        or interval_end_slot - interval_start_slot > MAX_SLOT_ROWS
        or not isinstance(reconciliation_pin, str)
        or not PIN.fullmatch(reconciliation_pin)
        or observation_source_pin is not None
        and (
            not isinstance(observation_source_pin, str) or not PIN.fullmatch(observation_source_pin)
        )
        or not isinstance(slot_accounting, dict)
        or not isinstance(fact_counts, dict)
        or not isinstance(fact_available_ns, dict)
        or not isinstance(horizons, (tuple, list))
        or not 1 <= len(horizons) <= MAX_HORIZONS
        or any(type(h) is not int or not 1 <= h <= MAX_SLOT_ROWS for h in horizons)
        or list(horizons) != sorted(set(horizons))
    ):
        raise ValueError("pumpswap_slot_target_contract")
    supports = _validated_support_rows(support_rows)
    if sorted(supports) != list(range(interval_start_slot, interval_end_slot)):
        raise ValueError("pumpswap_slot_target_interval_rows")
    for slot in supports:
        count = fact_counts.get(slot, 0)
        at = fact_available_ns.get(slot)
        if (
            type(count) is not int
            or count < 0
            or (at is not None and (type(at) is not int or at < 0))
        ):
            raise ValueError("pumpswap_slot_target_fact_accounting")
        summary = slot_accounting.get(str(slot))
        if not isinstance(summary, dict) or summary.get("slot") != slot:
            raise ValueError("pumpswap_slot_target_slot_accounting")
        if summary.get("positive_reference_facts") != count:
            raise ValueError("pumpswap_slot_target_fact_reconciliation")
        if summary.get("available_ns") != supports[slot]["available_ns"]:
            raise ValueError("pumpswap_slot_target_availability_reconciliation")
        if count > 0 and at is None:
            raise ValueError("pumpswap_slot_target_positive_without_availability")
        if (
            at is not None
            and supports[slot]["available_ns"] is not None
            and at > supports[slot]["available_ns"]
        ):
            raise ValueError("pumpswap_slot_target_late_fact")

    samples = []
    for origin in range(interval_start_slot, interval_end_slot):
        origin_row = supports[origin]
        for horizon in horizons:
            target_start = origin + 1
            target_end = origin + horizon + 1
            target_slots = list(range(target_start, target_end))
            outside = [slot for slot in target_slots if slot not in supports]
            available_support = [supports[slot] for slot in target_slots if slot in supports]
            lower_bound = sum(fact_counts.get(slot, 0) for slot in target_slots if slot in supports)
            lower_bound_at = max(
                (
                    fact_available_ns[slot]
                    for slot in target_slots
                    if slot in fact_available_ns and fact_counts.get(slot, 0) > 0
                ),
                default=None,
            )
            reasons = []
            evidence = {reconciliation_pin}
            if observation_source_pin is not None:
                evidence.add(observation_source_pin)
            all_slot_support_exact = not outside
            maturity_available = None
            if not outside and all(row["available_ns"] is not None for row in available_support):
                maturity_available = max(row["available_ns"] for row in available_support)
            target_support_available = []
            for slot in target_slots:
                if slot in outside:
                    reasons.append(
                        dict(slot=slot, status="pending", reason="outside_source_interval")
                    )
                    continue
                row = supports[slot]
                evidence.update(row["evidence"])
                evidence.update(row["proof_evidence"])
                status = row["status"]
                absent_proof = (
                    status != "complete"
                    and row["disposition"] == "block_absent_confirmed"
                    and row["enumeration_relation"] == "unlisted"
                    and row["enumeration_status"] == "complete"
                    and row["lineage_status"] == "linked"
                )
                support_available = (
                    row["proof_available_ns"] if absent_proof else row["available_ns"]
                )
                if support_available is None:
                    all_slot_support_exact = False
                else:
                    target_support_available.append(support_available)
                if status == "complete":
                    summary = slot_accounting[str(slot)]
                    trace_reasons = [
                        key for key in INCOMPLETE_TRACE_COUNTERS if summary.get(key, 0) > 0
                    ]
                    if trace_reasons:
                        all_slot_support_exact = False
                        reasons.append(
                            dict(slot=slot, status="unsupported", reason=";".join(trace_reasons))
                        )
                    else:
                        reasons.append(
                            dict(slot=slot, status="complete", reason="full_block_and_trace")
                        )
                elif absent_proof:
                    if fact_counts.get(slot, 0):
                        raise ValueError("pumpswap_slot_target_fact_on_skipped_slot")
                    reasons.append(
                        dict(
                            slot=slot,
                            status=status,
                            reason=row["reason"] or "lineage_bridged_absence",
                            transport_status=row["transport_status"],
                            enumeration_relation=row["enumeration_relation"],
                            enumeration_status=row["enumeration_status"],
                            lineage_status=row["lineage_status"],
                        )
                    )
                else:
                    all_slot_support_exact = False
                    reasons.append(
                        dict(
                            slot=slot,
                            status=status,
                            reason=row["reason"] or "support_unresolved",
                            transport_status=row["transport_status"],
                            providers=row.get("providers", {}),
                        )
                    )
            if outside:
                target_status = "pending"
                target_value = None
                bound = None
                target_available = None
            elif all_slot_support_exact and target_support_available:
                maturity_available = max(target_support_available)
                target_status = "exact"
                target_value = lower_bound
                bound = None
                target_available = maturity_available
            elif lower_bound > 0:
                target_status = "lower_bound"
                target_value = None
                bound = lower_bound
                target_available = lower_bound_at
            else:
                target_status = "masked"
                target_value = None
                bound = None
                target_available = None
            samples.append(
                dict(
                    origin_coordinate=origin,
                    target_window_start_coordinate=target_start,
                    target_window_end_coordinate_exclusive=target_end,
                    horizon_slots=horizon,
                    decision_available_ns=origin_row["available_ns"],
                    target_maturity_coordinate_exclusive=target_end,
                    target_maturity_available_ns=maturity_available,
                    target_status=target_status,
                    target_value=target_value,
                    lower_bound=bound,
                    target_available_ns=target_available,
                    lower_bound_available_ns=(
                        lower_bound_at if target_status == "lower_bound" else None
                    ),
                    target_reasons=reasons,
                    support_evidence=sorted(evidence),
                    role="exploratory",
                    split="unsplit",
                    split_eligibility={"train": False, "validation": False, "test": False},
                    fit_eligible=False,
                )
            )
    return samples


def experience_loss_adapter(sample):
    """Map logical statuses to exact-count and positive-censor-loss inputs; never fits."""
    status = sample.get("target_status")
    value = sample.get("target_value")
    bound = sample.get("lower_bound")
    if status == "exact":
        if type(value) is not int or value < 0 or bound is not None:
            raise ValueError("pumpswap_slot_target_exact_adapter")
        return dict(exact_target=value, exact_mask=True, lower_bound=0, lower_bound_mask=False)
    if status == "lower_bound":
        if type(bound) is not int or bound <= 0 or value is not None:
            raise ValueError("pumpswap_slot_target_bound_adapter")
        return dict(exact_target=0, exact_mask=False, lower_bound=bound, lower_bound_mask=True)
    if status in {"masked", "pending"}:
        if value is not None or bound is not None:
            raise ValueError("pumpswap_slot_target_masked_adapter")
        return dict(exact_target=0, exact_mask=False, lower_bound=0, lower_bound_mask=False)
    raise ValueError("pumpswap_slot_target_status")


def coordinate_sample_schema():
    """Self-describing native-slot adapter over the shared dense v3 experience layout."""
    from memecoin_experience.coordinate_samples import schema

    return schema(
        context=1,
        features=[
            dict(
                name="recognized_instruction_references_at_origin",
                unit="references",
                modality="instruction",
            )
        ],
        targets=[
            dict(
                name="next_slot_recognized_instruction_references",
                unit="references",
                kind="observed_process_count@1slots",
            )
        ],
        availability="historical_replay",
        representation_name="pumpswap-retrospective-native-slot-count-v1",
        representation_version="1",
        coordinate_domain=slot_invocations.DOMAIN,
    )


def coordinate_samples_from_targets(
    samples,
    *,
    source_pin,
    reconciliation_pin,
    facts_by_slot,
    support_by_slot,
    cutoff_ns,
):
    """Map the bounded logical target receipt into the shared checked v3 sample contract."""
    from memecoin_experience.coordinate_samples import domain_registry

    schema = coordinate_sample_schema()
    domain_pin = schema["coordinate_domain_pin"]
    if (
        not isinstance(samples, list)
        or not samples
        or any(not PIN.fullmatch(pin) for pin in (source_pin, reconciliation_pin))
        or type(cutoff_ns) is not int
        or cutoff_ns < 0
    ):
        raise ValueError("pumpswap_coordinate_sample_inputs")
    if sorted(domain_registry(schema)) != [domain_pin]:
        raise ValueError("pumpswap_coordinate_sample_domain")
    rows = []
    for sample in samples:
        origin = sample["origin_coordinate"]
        feature = facts_by_slot.get(origin, {"count": 0, "available_ns": None})
        support = support_by_slot.get(origin)
        if not isinstance(support, dict):
            raise ValueError("pumpswap_coordinate_origin_support")
        count, fact_at = feature.get("count"), feature.get("available_ns")
        if type(count) is not int or count < 0:
            raise ValueError("pumpswap_coordinate_feature_count")
        if fact_at is not None and (type(fact_at) is not int or fact_at < 0):
            raise ValueError("pumpswap_coordinate_feature_availability")
        status = support.get("status")
        trace_incomplete = any(support.get(name, 0) > 0 for name in INCOMPLETE_TRACE_COUNTERS)
        skipped_exact = (
            support.get("disposition") == "block_absent_confirmed"
            and support.get("enumeration_relation") == "unlisted"
            and support.get("enumeration_status") == "complete"
            and support.get("lineage_status") == "linked"
            and support.get("proof_available_ns") is not None
        )
        support_at = (
            support.get("proof_available_ns") if skipped_exact else support.get("available_ns")
        )
        if support_at is not None and (type(support_at) is not int or support_at < 0):
            raise ValueError("pumpswap_coordinate_support_availability")
        if count > 0:
            feature_value = float(count)
            fact_dependency_at = fact_at
        elif (
            status == "complete"
            and not trace_incomplete
            and support_at is not None
            or skipped_exact
        ):
            feature_value = 0.0
            fact_dependency_at = None
        else:
            feature_value = None
            fact_dependency_at = None
        dependencies = []
        if fact_dependency_at is not None:
            dependencies.append(
                dict(
                    artifact_sha=source_pin,
                    domain_pin=domain_pin,
                    start_coordinate=origin,
                    end_coordinate_exclusive=origin + 1,
                    available_ns=fact_dependency_at,
                )
            )
        if support_at is not None:
            dependencies.append(
                dict(
                    artifact_sha=reconciliation_pin,
                    domain_pin=domain_pin,
                    start_coordinate=origin,
                    end_coordinate_exclusive=origin + 1,
                    available_ns=support_at,
                )
            )
        if not dependencies:
            raise ValueError("pumpswap_coordinate_origin_unavailable")
        input_available = max(dep["available_ns"] for dep in dependencies)
        target_status = sample["target_status"]
        target_value = sample["target_value"]
        target_available = sample["target_available_ns"]
        details = {}
        if target_status == "lower_bound":
            details = {
                "lower_bound": sample["lower_bound"],
                "lower_bound_available_ns": sample["lower_bound_available_ns"],
            }
            target_available = None
        completion = (
            "complete"
            if target_status == "exact"
            else "incomplete"
            if target_status == "lower_bound"
            else "unknown"
        )
        reasons = [
            f"slot:{item['slot']}:{item['status']}:{item['reason']}"
            for item in sample["target_reasons"]
        ]
        provenance = sorted({source_pin, reconciliation_pin, *sample["support_evidence"]})
        rows.append(
            dict(
                id=f"{slot_invocations.PROGRAM_ENTITY}:{origin}",
                pool=slot_invocations.PROGRAM_ENTITY,
                origin=dict(domain_pin=domain_pin, coordinate=origin, position=[]),
                decision_ns=None,
                input_dependencies=dependencies,
                input_available_ns=input_available,
                values=[[feature_value]],
                targets=[target_value],
                target_status=[target_status],
                target_windows=[
                    dict(
                        domain_pin=domain_pin,
                        start_coordinate=sample["target_window_start_coordinate"],
                        end_coordinate_exclusive=sample["target_window_end_coordinate_exclusive"],
                        maturity_coordinate_exclusive=sample[
                            "target_maturity_coordinate_exclusive"
                        ],
                        completion_status=completion,
                        completion_evidence=(provenance if target_status == "exact" else []),
                        support_reasons=reasons,
                    )
                ],
                target_available_ns=[target_available],
                target_details=[details],
                provenance=provenance,
                origin_support_status=status,
                origin_support_reason=support.get("reason", ""),
                role="exploratory",
                split="unsplit",
                fit_eligible=False,
            )
        )
    return schema, rows


def publish_coordinate_snapshot(audit_store, audit_pin, snapshot_store):
    """Publish the real logical samples through shared v3 storage; this path never fits."""
    from memecoin_experience import coordinate_samples
    from memecoin_experience.observations import Source
    from memecoin_experience.storage import Snapshot as ExperienceSnapshot
    from memecoin_experience.storage import build

    from . import slot_invocations

    audit_store = safe_path(audit_store)
    aggregate = slot_invocations.load_reference_audit(audit_store, audit_pin)
    target_receipt = _build_from_audit(audit_store, audit_pin)
    reconciliation_store = safe_path(audit_store / "reconciliation")
    reconciliation = block_reconciliation.load_block_reconciliation(
        reconciliation_store, aggregate["reconciliation_pin"]
    )
    support_rows = [
        row
        for reference in reconciliation["pages"]
        for row in json.loads(
            (
                reconciliation_store
                / "qualification-block-reconciliation-pages"
                / f"{reference['pin']}.json"
            ).read_text()
        )
    ]
    accounting = aggregate["counts"].get("slot_accounting")
    if not isinstance(accounting, dict):
        raise ValueError("pumpswap_coordinate_slot_accounting")
    support_by_slot = {row["slot"]: row for row in support_rows}
    for slot, row in support_by_slot.items():
        summary = accounting.get(str(slot))
        if not isinstance(summary, dict) or summary.get("slot") != slot:
            raise ValueError("pumpswap_coordinate_slot_accounting")
        row.update({name: summary.get(name, 0) for name in INCOMPLETE_TRACE_COUNTERS})
    source_store = safe_path(audit_store / "observations")
    source_pin = aggregate["observation_source_pin"]
    if source_pin is None:
        raise ValueError("pumpswap_coordinate_observation_source_missing")
    source = Source(source_store, source_pin, role="train")
    if source.schema != slot_invocations.OBSERVATION_SCHEMA:
        raise ValueError("pumpswap_coordinate_observation_schema")
    facts_by_slot = {}
    start = reconciliation["interval"]["start_slot"]
    end = reconciliation["interval"]["end_slot"]
    for row in source.rows():
        if (
            row["entity"] != slot_invocations.PROGRAM_ENTITY
            or row["coordinate_domain_pin"] != slot_invocations.DOMAIN_PIN
            or not row["stream"].startswith("pumpswap_instruction_reference/")
            or row["coordinate"] not in range(start, end)
            or row["values"] != [1.0]
        ):
            raise ValueError("pumpswap_coordinate_observation_fact")
        slot = row["coordinate"]
        fact = facts_by_slot.setdefault(slot, {"count": 0, "available_ns": None})
        fact["count"] += 1
        fact["available_ns"] = max(fact["available_ns"] or 0, row["available_ns"])
    schema, rows = coordinate_samples_from_targets(
        target_receipt["samples"],
        source_pin=source_pin,
        reconciliation_pin=aggregate["reconciliation_pin"],
        facts_by_slot=facts_by_slot,
        support_by_slot=support_by_slot,
        cutoff_ns=source.manifest["binding"]["cutoff_ns"],
    )
    snapshot_store = safe_path(snapshot_store)
    if not snapshot_store.is_relative_to(safe_path(Path.cwd() / "work")):
        raise ValueError("pumpswap_coordinate_snapshot_must_be_work")
    result = build(
        snapshot_store,
        rows,
        sources=[
            dict(kind="pumpswap_reference_audit", pin=audit_pin),
            dict(kind="observation_source", pin=source_pin),
            dict(kind="block_reconciliation", pin=aggregate["reconciliation_pin"]),
        ],
        cutoff=source.manifest["binding"]["cutoff_ns"],
        schema=schema,
        role="research_evaluation",
        chunk=1,
    )
    snapshot = ExperienceSnapshot(snapshot_store, result["snapshot"])
    start, end = rows[0]["origin"]["coordinate"], rows[-1]["origin"]["coordinate"] + 1
    cutoff = source.manifest["binding"]["cutoff_ns"]
    plan = coordinate_samples.make_split_plan(
        schema["coordinate_domain_pin"],
        {
            "train": {
                "intervals": [[start, end]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": cutoff,
            },
            "validation": {
                "intervals": [],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": cutoff,
            },
            "test": {"intervals": [], "dependency_intervals": {}, "knowledge_cutoff_ns": cutoff},
        },
    )
    outcomes = [coordinate_samples.eligibility(row, schema, plan, "train") for row in rows]
    exact_matured = sum(any(result["exact_mask"]) for result in outcomes)
    summary = dict(
        snapshot=result["snapshot"],
        schema_pin=coordinate_samples.digest(schema),
        rows=snapshot.manifest["decisions"],
        exact_matured_examples=exact_matured,
        exact_eligible_by_role={
            role: sum(
                any(coordinate_samples.eligibility(row, schema, plan, role)["exact_mask"])
                for row in rows
            )
            for role in ("train", "validation", "test")
        },
        split_infeasible=True,
        fit_allowed=False,
        role="research_evaluation",
        decision_ns_fabricated=False,
    )
    return summary


def _build_from_audit(audit_store, audit_pin):
    aggregate = slot_invocations.load_reference_audit(audit_store, audit_pin)
    reconciliation_store = safe_path(audit_store / "reconciliation")
    reconciliation = block_reconciliation.load_block_reconciliation(
        reconciliation_store, aggregate["reconciliation_pin"]
    )
    start = reconciliation["interval"]["start_slot"]
    end = reconciliation["interval"]["end_slot"]
    support_rows = [
        row
        for reference in reconciliation["pages"]
        for row in json.loads(
            (
                reconciliation_store
                / "qualification-block-reconciliation-pages"
                / f"{reference['pin']}.json"
            ).read_text()
        )
    ]
    counts = defaultdict(int)
    fact_availability = {}
    if aggregate["observation_source_pin"] is None:
        if aggregate["counts"].get("positive_reference_facts", 0):
            raise ValueError("pumpswap_slot_target_positive_without_observation_source")
    else:
        source = observations.Source(
            audit_store / "observations", aggregate["observation_source_pin"], role="train"
        )
        if source.schema != slot_invocations.OBSERVATION_SCHEMA:
            raise ValueError("pumpswap_slot_target_observation_schema")
        for row in source.rows():
            if (
                row["entity"] != slot_invocations.PROGRAM_ENTITY
                or row["coordinate_domain_pin"] != slot_invocations.DOMAIN_PIN
                or not row["stream"].startswith("pumpswap_instruction_reference/")
                or row["coordinate"] not in range(start, end)
                or row["values"] != [1.0]
            ):
                raise ValueError("pumpswap_slot_target_observation_fact")
            counts[row["coordinate"]] += 1
            fact_availability[row["coordinate"]] = max(
                fact_availability.get(row["coordinate"], 0), row["available_ns"]
            )
    accounting = aggregate["counts"].get("slot_accounting")
    if not isinstance(accounting, dict):
        raise ValueError("pumpswap_slot_target_slot_accounting")
    samples = build_logical_samples(
        interval_start_slot=start,
        interval_end_slot=end,
        support_rows=support_rows,
        slot_accounting=accounting,
        fact_counts=counts,
        fact_available_ns=fact_availability,
        reconciliation_pin=aggregate["reconciliation_pin"],
        observation_source_pin=aggregate["observation_source_pin"],
        horizons=(1,),
    )
    matured = sum(sample["target_status"] == "exact" for sample in samples)
    status_counts = Counter(sample["target_status"] for sample in samples)
    receipt = dict(
        version=VERSION,
        contract=CONTRACT,
        contract_pin=CONTRACT_PIN,
        capability="retrospective_native_slot_count_targets",
        reconciliation_pin=aggregate["reconciliation_pin"],
        process_pin=aggregate["process_pin"],
        observation_source_pin=aggregate["observation_source_pin"],
        source_role="train",
        accounting_unit="recognized_instruction_references",
        target_horizons_slots=[1],
        samples=samples,
        sample_status_counts=dict(sorted(status_counts.items())),
        eligibility_report=dict(
            fit_allowed=False,
            matured_exact_examples=matured,
            independent_train_validation_test_partitions=False,
            reason="two_slot_episode_has_only_one_matured_example_and_is_retrospective",
        ),
        data_scope=dict(
            support_created=False,
            observed_zero_is_claimed_only_for_exact_qualified_target=True,
            per_pool_targets=False,
            historical_pit_forecast=False,
            paths_urls_pool_ids_and_signatures_in_receipt=False,
        ),
    )
    return receipt


def publish_slot_targets(audit_store, audit_pin, target_store):
    target_store = safe_path(target_store)
    if not target_store.is_relative_to(safe_path(Path.cwd() / "work")):
        raise ValueError("pumpswap_slot_target_store_must_be_work")
    receipt = _build_from_audit(safe_path(audit_store), audit_pin)
    pin = digest(receipt)
    target_store.mkdir(parents=True, exist_ok=True)
    with lock(target_store / ".slot-targets.lock"):
        receipt_dir = safe_path(target_store / RECEIPTS)
        receipt_dir.mkdir(exist_ok=True)
        path = receipt_dir / f"{pin}.json"
        if path.exists():
            raise ValueError("pumpswap_slot_target_exists")
        atomic_json(path, receipt)
        sync_dir(receipt_dir)
    load_slot_targets(target_store, pin, audit_store, audit_pin)
    return pin


def load_slot_targets(target_store, pin, audit_store, audit_pin):
    if not isinstance(pin, str) or not PIN.fullmatch(pin):
        raise ValueError("pumpswap_slot_target_pin")
    target_store = safe_path(target_store)
    path = safe_path(target_store / RECEIPTS / f"{pin}.json")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8_000_000:
        raise ValueError("pumpswap_slot_target_file")
    receipt = json.loads(path.read_text())
    if digest(receipt) != pin:
        raise ValueError("pumpswap_slot_target_checksum")
    expected = _build_from_audit(safe_path(audit_store), audit_pin)
    if expected != receipt:
        raise ValueError("pumpswap_slot_target_replay")
    return receipt
