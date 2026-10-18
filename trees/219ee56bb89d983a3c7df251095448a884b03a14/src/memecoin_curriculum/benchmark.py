"""Offline benchmark role/support preflight. Not an admission or promotion authority."""

from collections import Counter, defaultdict
from pathlib import Path

from memecoin_ml.data import read, sha
from memecoin_registry.contracts import digest, verify


def readiness(config, assignments):
    """Audit sealed metadata packets without reading raw targets or changing any runtime."""
    if config["schema"] != "brain-benchmark-v1":
        raise ValueError("benchmark_schema")
    banned = {row["manifest_sha"] for row in config["exposure_registry"]}
    roles = config["purposes"]
    if roles != ["development", "calibration", "prospective"]:
        raise ValueError("benchmark_roles")
    if config["horizons_minutes"] != [5, 15] or not config["required_support"]:
        raise ValueError("benchmark_support_contract")
    packets, intervals, identities = {}, [], set()
    for assignment in assignments:
        root = Path(assignment["path"])
        verify(root)
        manifest = sha(root / "manifest.json")
        if manifest != assignment["manifest_sha"] or manifest in banned:
            raise ValueError("benchmark_manifest_or_exposure")
        packet = read(root / "benchmark-input.json")
        purpose = packet["purpose"]
        if purpose not in roles or purpose in packets:
            raise ValueError("benchmark_duplicate_or_forbidden_role")
        if packet["source_role"] in config["protected_roles"]:
            raise ValueError("benchmark_protected")
        if not packet["source_manifest_shas"] or banned.intersection(
            packet["source_manifest_shas"]
        ):
            raise ValueError("benchmark_exposed_source")
        if packet["labels_exposed"] or packet["used_for_fit"]:
            raise ValueError("benchmark_previously_used")
        start, end = packet["start_s"], packet["end_s"]
        if not 0 < start < end or packet["enrollment_frozen_ns"] > start * 10**9:
            raise ValueError("benchmark_window_or_enrollment")
        if packet["cutoff_ns"] < end * 10**9:
            raise ValueError("benchmark_window_incomplete")
        train_mints = set(packet["reference_train_mints"])
        if packet["reference_train_mints_sha"] != digest(sorted(train_mints)):
            raise ValueError("benchmark_train_membership")
        if purpose == "prospective" and not (
            packet["models_frozen_ns"] < start * 10**9
            and packet["calibration_frozen_ns"] < start * 10**9
        ):
            raise ValueError("benchmark_future_freeze")
        rows = packet["rows"]
        local = set()
        for row in rows:
            key = (row["lane"], row["pool"], row["origin_s"], row["horizon_minutes"])
            identity = (row["pool"], row["origin_s"], row["horizon_minutes"])
            if key in local or identity in identities:
                raise ValueError("benchmark_duplicate_or_split_overlap")
            local.add(key)
            if row["lane"] not in ("auto", "watch") or row["horizon_minutes"] not in (5, 15):
                raise ValueError("benchmark_lane_or_horizon")
            if not row["mint"] or not start <= row["origin_s"] < end:
                raise ValueError("benchmark_origin")
            if row["seen"] != ("seen" if row["mint"] in train_mints else "unseen"):
                raise ValueError("benchmark_seen_membership")
            if row["status"] not in ("scored", "pending", "stale", "unsupported", "missing"):
                raise ValueError("benchmark_status")
            if row["status"] != "scored":
                continue
            if not start <= row["input_start_s"] < row["origin_s"] < row["maturity_s"] <= end:
                raise ValueError("benchmark_dependency_interval")
            if row["maturity_s"] != row["origin_s"] + row["horizon_minutes"] * 60:
                raise ValueError("benchmark_maturity")
            if not (
                row["origin_s"] * 10**9
                <= row["input_available_ns"]
                <= row["predicted_ns"]
                <= packet["cutoff_ns"]
                and 0 < row["target_available_ns"] <= packet["cutoff_ns"]
            ):
                raise ValueError("benchmark_availability")
            if row["target_available_ns"] < row["maturity_s"] * 10**9:
                raise ValueError("benchmark_premature_label")
            if purpose == "prospective" and not (
                row["predicted_ns"] < row["maturity_s"] * 10**9
                and row["predicted_ns"] < row["target_available_ns"]
            ):
                raise ValueError("benchmark_backdated_forecast")
            birth = row["birth_s"]
            age = "unknown" if birth is None else age_bucket(row["origin_s"] - birth)
            if row["age"] != age:
                raise ValueError("benchmark_age")
        identities.update((r["pool"], r["origin_s"], r["horizon_minutes"]) for r in rows)
        packets[purpose] = packet
        intervals.append((roles.index(purpose), start, end))
    if len({p["reference_train_mints_sha"] for p in packets.values()}) > 1:
        raise ValueError("benchmark_reference_membership_changed")
    ordered = sorted(intervals)
    if any(a[2] > b[1] for a, b in zip(ordered[:-1], ordered[1:], strict=True)):
        raise ValueError("benchmark_temporal_overlap")
    reports, missing = {}, []
    for role in roles:
        if role not in packets:
            missing.append(f"missing_packet:{role}")
            continue
        rows = packets[role]["rows"]
        report = {"scheduled": len(rows), "scored": sum(r["status"] == "scored" for r in rows)}
        report["statuses"] = dict(Counter(r["status"] for r in rows))
        report["cells"] = []
        for requirement in config["required_support"]:
            for horizon in config["horizons_minutes"]:
                selected = [
                    r
                    for r in rows
                    if r["status"] == "scored"
                    and r["horizon_minutes"] == horizon
                    and all(
                        requirement[k] == "any" or r[k] == requirement[k]
                        for k in ("lane", "seen", "age")
                    )
                ]
                support = cell_support(selected)
                floors = {k: requirement[k] for k in ("mints", "days", "windows")}
                floors["days"] = min(floors["days"], config["day_floor_caps"][role])
                sufficient = all(support[k] >= floors[k] for k in floors)
                name = "/".join(
                    [
                        role,
                        requirement["lane"],
                        requirement["seen"],
                        requirement["age"],
                        str(horizon),
                    ]
                )
                if not sufficient:
                    missing.append(f"insufficient:{name}")
                report["cells"].append(dict(name=name, support=support, sufficient=sufficient))
        reports[role] = report
    return dict(
        schema="brain-benchmark-readiness-v1",
        support_ready=not missing,
        reasons=missing,
        roles=reports,
        raw_receipt_verification_required=True,
        admission_ready=False,
        promotion=False,
        config_sha=digest(config),
    )


def age_bucket(seconds):
    if seconds < 0:
        raise ValueError("benchmark_future_birth")
    return "lt1d" if seconds < 86400 else "1to7d" if seconds < 7 * 86400 else "ge7d"


def cell_support(rows):
    """Mint-level dependency windows avoid double-counting related pools or overlap."""
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["mint"]].append(row)
    windows = 0
    for part in grouped.values():
        end = -1
        for row in sorted(part, key=lambda r: r["maturity_s"]):
            if row["input_start_s"] >= end:
                windows += 1
                end = row["maturity_s"]
    return dict(
        mints=len(grouped),
        days=len({r["origin_s"] // 86400 for r in rows}),
        windows=windows,
        origins=len(rows),
        independent_n=None,
    )
