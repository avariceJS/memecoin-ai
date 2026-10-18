"""Read-only source probe and exact, non-authorizing activation candidate."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from memecoin_ai.causal import CausalBook, observed_checkpoint
from memecoin_ai.sources import Capture, HTTPSourceError, ReadOnlyRPC, TransportSourceError
from memecoin_ml.data import read, sha, write
from memecoin_registry.contracts import inventory, seal, verify
from memecoin_sampling.contract import NS, load, paths_disjoint, sources

Row = dict[str, Any]


def endpoint(path: Path) -> str:
    settings = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            settings[key.strip()] = value.strip().strip("\"'")
    value = settings.get("MEMECOIN_ALCHEMY_HTTP_URL", "")
    parsed = urlsplit(value)
    if (parsed.scheme, parsed.netloc) != ("https", "solana-mainnet.g.alchemy.com"):
        raise ValueError("missing_or_wrong_runtime_origin")
    if not parsed.path.startswith("/v2/") or len(parsed.path) <= 4 or parsed.query:
        raise ValueError("invalid_runtime_endpoint")
    return value


def identity(value: str) -> str:
    # High-entropy credential fingerprint, never the URL or key itself.
    return hashlib.sha256(value.encode()).hexdigest()


def runtime_identity() -> Row:
    return dict(
        sources={
            **sources(),
            **{
                "memecoin_collector/" + p.name: sha(p)
                for p in sorted(Path(__file__).parent.glob("*.py"))
            },
        },
        python_version=sys.version,
        python_executable=str(Path(sys.executable).resolve()),
        python_sha256=sha(Path(sys.executable).resolve()),
        platform=platform.platform(),
        machine=platform.machine(),
    )


def economics(binding: Row) -> Row:
    months = Counter(
        datetime.fromtimestamp(w["due_ns"] / NS, UTC).strftime("%Y-%m") for w in binding["windows"]
    )
    return dict(
        method="getMultipleAccounts",
        documented_cu_per_rpc=20,
        max_rpc=60480,
        max_cu=1209600,
        cu_per_window=3600,
        monthly={m: dict(windows=n, rpc=n * 180, cu=n * 3600) for m, n in months.items()},
        nominal_cu_per_second=12,
        instantaneous_three_request_cu=60,
        # Existing core inserts >=1s between calls; no retry bursts.
        free_tier_documented_monthly_cu=30000000,
        actual_account_plan=None,
        actual_account_hard_usd_limit=None,
        authorized_paid_usd=0,
        actual_future_usd=None,
        public_payg_estimate_usd=1.2096 * 0.525,
        public_payg_rate_per_million_cu=0.525,
        shared_account_usage_must_be_reserved=True,
        raw_response_upper_bytes=60480 * 1000000,
        source_plus_registry_raw_upper_bytes=(60480 + 224 * 180) * 1000000,
        upper_excludes_metadata_targets_dossiers=True,
    )


def stage(
    proposal: Path, output: Path, references: list[Path], credential: str, evidence: list[Path]
) -> Row:
    binding, _ = load(proposal)
    if Path(binding["source_root"]).exists():
        raise ValueError("source_root_already_exists_no_activation")
    paths_disjoint(output, proposal)
    paths_disjoint(output, Path(binding["source_root"]))
    pools = [json.loads(Capture.load(p).request_json)["params"][0][0] for p in references]
    if pools != binding["source_semantics"]["campaign"]["pools"]:
        raise ValueError("reference_cohort_changed")
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(proposal, output / "proposal")
    refs = output / "references"
    refs.mkdir()
    for i, p in enumerate(references):
        Capture.load(p).save(refs / f"{i:03}")
    result = dict(
        schema=1,
        stage="D49",
        status="staged_not_authorized",
        created_ns=time.time_ns(),
        proposal_sha256=sha(proposal / "binding.json"),
        proposal_inventory=inventory(proposal),
        source_root=binding["source_root"],
        credential_sha256=identity(credential),
        runtime=runtime_identity(),
        reference_inventory=inventory(refs),
        evidence={str(p.resolve()): sha(p) for p in evidence},
        budget=economics(binding),
        free_disk_bytes=shutil.disk_usage(output).free,
        activation_decision=None,
        collection_authorized=False,
        research_launch_authorized=False,
        holdout_release_authorized=False,
        blockers=[
            "account_plan_and_zero_dollar_hard_cap_unverified",
            "runtime_credential_to_billing_account_mapping_unverified",
            "calendar_service_and_wake_uptime_unqualified",
            "full_live_window_not_qualified",
            "separate_activation_decision_absent",
        ],
    )
    write(output / "candidate.json", result)
    seal(output)
    return result


def verify_candidate(root: Path, credential: str) -> Row:
    verify(root)
    binding, _ = load(root / "proposal")
    result: Row = read(root / "candidate.json")
    if (
        result["runtime"] != runtime_identity()
        or result["credential_sha256"] != identity(credential)
        or result["proposal_sha256"] != sha(root / "proposal/binding.json")
        or result["proposal_inventory"] != inventory(root / "proposal")
        or result["reference_inventory"] != inventory(root / "references")
        or result["source_root"] != binding["source_root"]
        or result["budget"] != economics(binding)
        or any(sha(Path(p)) != s for p, s in result["evidence"].items())
        or result["research_launch_authorized"] is not False
        or result["holdout_release_authorized"] is not False
        or result["collection_authorized"] is not False
        or result["activation_decision"] is not None
    ):
        raise ValueError("activation_binding_changed")
    return result


def probe(root: Path, references: list[Capture], client: ReadOnlyRPC) -> Row:
    pools = [json.loads(c.request_json)["params"][0][0] for c in references]
    book = CausalBook(
        pools, max_receipt_age_ns=25 * NS, max_chain_age_ns=45 * NS, max_gap_ns=25 * NS
    )
    rows = []
    started = time.monotonic()
    for n in range(6):
        if n == 3:
            time.sleep(max(0, 5 - (time.monotonic() - started)))
        elif n:
            time.sleep(1)
        row: Row = dict(index=n, pool=pools[n % 3], started_ns=time.time_ns())
        try:
            request = json.loads(references[n % 3].request_json)
            cap = client.fetch(request["method"], request["params"])
            cap.save(root / "captures" / f"{n:03}")
            obs = observed_checkpoint(cap, row["pool"], time.time_ns(), config_layout_version=2)
            status = book.ingest(obs)
            feature = next(
                f for f in book.features(obs["available_ns"]) if f["pool_id"] == row["pool"]
            )
            problems = [r for r in feature["reasons"] if r != "history_warmup"]
            row.update(
                status=status,
                problems=problems,
                slot=obs["slot"],
                received_ns=cap.received_ns,
                available_ns=obs["available_ns"],
                chain_timestamp_seconds=obs["chain_timestamp_seconds"],
                chain_age_seconds=(obs["available_ns"] / NS - obs["chain_timestamp_seconds"]),
                response_bytes=len(cap.response),
                response_seconds=(cap.received_ns - cap.request_started_ns) / NS,
            )
            if problems or status not in ("advanced", "duplicate"):
                row.update(status="failed", category="freshness_or_coherence")
        except Exception as exc:
            row.update(
                status="failed",
                error_type=type(exc).__name__,
                category=exc.reason
                if isinstance(exc, TransportSourceError)
                else "http_error"
                if isinstance(exc, HTTPSourceError)
                else "checkpoint_validation",
            )
            if isinstance(exc, HTTPSourceError):
                row["http_status"] = exc.status
        row["transport_measurement"] = client.transport_measurement()
        write(root / f"attempt-{n}.json", row)
        rows.append(row)
        print(json.dumps(row), flush=True)
        if row["status"] == "failed":
            break
    result = dict(
        attempts=rows,
        requests=len(rows),
        nominal_cu=len(rows) * 20,
        complete=len(rows) == 6 and all(r["status"] != "failed" for r in rows),
        elapsed_seconds=time.monotonic() - started,
        qualification="source_probe_only",
        F01_admitted_windows=0,
        monetary_cost_measured=False,
    )
    write(root / "result.json", result)
    return result


def reproduce_probe(root: Path) -> Row:
    """Offline replay at the original measured availability, never current time."""
    verify(root)
    result = read(root / "result.json")
    count = 0
    for row in result.get("attempts", []):
        if row["status"] == "failed":
            continue
        cap = Capture.load(root / "captures" / f"{row['index']:03}")
        obs = observed_checkpoint(cap, row["pool"], row["available_ns"], config_layout_version=2)
        expected = dict(
            slot=obs["slot"],
            received_ns=obs["received_ns"],
            available_ns=obs["available_ns"],
            chain_timestamp_seconds=obs["chain_timestamp_seconds"],
            response_bytes=len(cap.response),
        )
        if any(row[key] != value for key, value in expected.items()):
            raise ValueError("probe_raw_reproduction_changed")
        count += 1
    return dict(exact=True, reproduced_checkpoints=count, real_rpc=0)
