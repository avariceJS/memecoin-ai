"""Sealed runtime and reviewed zero-money/activation receipts, without issuing them."""

from __future__ import annotations

import json
import plistlib
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from memecoin_ai.sources import Capture
from memecoin_collector.qualification import identity, runtime_identity
from memecoin_ml.data import read, sha, write
from memecoin_registry.contracts import inventory, seal, verify
from memecoin_sampling.contract import load, paths_disjoint

Row = dict[str, Any]
FLOOR = 2 * 1024**3
RESERVE = 512 * 1024**2


class GateError(ValueError):
    """Only controlled categories, never provider payload/credential text."""


def code_identity() -> Row:
    result = runtime_identity()
    result["sources"].update(
        {f"memecoin_operational/{p.name}": sha(p) for p in Path(__file__).parent.glob("*.py")}
    )
    return result


def no_symlink(path: Path) -> None:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise GateError("symlink_path")


def stage(
    proposal: Path,
    output: Path,
    refs: list[Path],
    *,
    mode: str,
    credential_sha: str | None = None,
    settings: Path | None = None,
) -> Row:
    if mode not in ("synthetic_fixture", "prospective"):
        raise GateError("unknown_mode")
    binding, _ = load(proposal)
    for path in (proposal, output, Path(binding["source_root"])):
        no_symlink(path)
    paths_disjoint(output, proposal)
    paths_disjoint(output, Path(binding["source_root"]))
    if Path(binding["source_root"]).exists():
        raise GateError("source_already_exists")
    if mode == "prospective" and (settings is None or credential_sha is None):
        raise GateError("runtime_credentials_not_bound")
    captures = [Capture.load(p) for p in refs]
    if [json.loads(c.request_json)["params"][0][0] for c in captures] != binding[
        "source_semantics"
    ]["campaign"]["pools"]:
        raise GateError("reference_cohort")
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(proposal, output / "proposal")
    for i, cap in enumerate(captures):
        cap.save(output / "references" / f"{i:03}")
    code = code_identity()
    source = Path(__file__).parent.parent
    for name in code["sources"]:
        dest = output / "runtime" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, dest)
    config = dict(
        schema=1,
        stage="D50",
        mode=mode,
        code=code,
        proposal_sha=sha(proposal / "binding.json"),
        source_root=binding["source_root"],
        credential_sha=credential_sha,
        settings=str(settings.resolve()) if settings else None,
        segment_seconds=210,
        window_seconds=600,
        free_floor=FLOOR,
        window_bytes=RESERVE,
        clock_drift_seconds=2,
        activation_authorized=False,
        research_authorized=False,
        created_ns=time.time_ns(),
    )
    write(output / "config.json", config)
    seal(output)
    return config


def check_bundle(bundle: Path, pin: str) -> Row:
    no_symlink(bundle)
    if sha(bundle / "manifest.json") != pin:
        raise GateError("bundle_pin_changed")
    verify(bundle)
    c: Row = read(bundle / "config.json")
    binding, _ = load(bundle / "proposal")
    if (
        c["schema"] != 1
        or c["code"] != code_identity()
        or c["proposal_sha"] != sha(bundle / "proposal/binding.json")
        or c["source_root"] != binding["source_root"]
        or c["activation_authorized"] is not False
        or c["research_authorized"] is not False
        or (
            c["segment_seconds"],
            c["window_seconds"],
            c["free_floor"],
            c["window_bytes"],
            c["clock_drift_seconds"],
        )
        != (210, 600, FLOOR, RESERVE, 2)
        or inventory(bundle / "runtime") != c["code"]["sources"]
    ):
        raise GateError("runtime_contract_changed")
    return c


def authorize(
    bundle: Path,
    pin: str,
    receipt: Path | None,
    receipt_pin: str | None,
    *,
    scope: str,
    credential: str,
    now_ns: int,
) -> Row:
    c = check_bundle(bundle, pin)
    if receipt is None or receipt_pin is None:
        raise GateError("separate_decision_required")
    no_symlink(receipt)
    if sha(receipt) != receipt_pin:
        raise GateError("receipt_pin_changed")
    r: Row = read(receipt)
    review = r.get("budget_review", {})
    if (
        c["mode"] != "prospective"
        or r.get("scope") != scope
        or r.get("bundle_sha") != pin
        or r.get("proposal_sha") != c["proposal_sha"]
        or r.get("decision") != "approved"
        or not r.get("decision_reference")
        or review.get("credential_sha") != c["credential_sha"]
        or identity(credential) != c["credential_sha"]
        or review.get("plan") != "free"
        or review.get("paid_limit_usd") != 0
        or review.get("no_automatic_paid_overage") is not True
        or not review.get("account_id")
        or not review.get("review_reference")
        or not review.get("mapping_evidence_sha")
        or not review.get("billing_evidence_sha")
    ):
        raise GateError("unreviewed_no_charge_or_identity")
    start, end = review.get("observed_ns"), review.get("valid_until_ns")
    if type(start) is not int or type(end) is not int or not start <= now_ns < end:
        raise GateError("budget_review_expired")
    maximum = 180 if scope == "bounded_qualification" else 60480 if scope == "calendar" else 0
    if scope == "bounded_qualification":
        attempt = r.get("attempt_root")
        if not isinstance(attempt, str) or not Path(attempt).is_absolute():
            raise GateError("single_attempt_root_required")
        no_symlink(Path(attempt))
    needed = maximum * 20
    remaining = review.get("reserved_available_cu")
    if (
        maximum == 0
        or r.get("max_rpc") != maximum
        or type(remaining) is not int
        or remaining < needed
    ):
        raise GateError("quota_not_reserved")
    # Digests are externally pinned attestations, not independent proof of provider billing.
    for field in ("mapping_evidence_sha", "billing_evidence_sha"):
        path = Path(review[field.removesuffix("_sha") + "_path"])
        no_symlink(path)
        if sha(path) != review[field]:
            raise GateError("budget_evidence_changed")
    return r


def check_attempt(bundle: Path, config: Row, decision: Row, output: Path, scope: str) -> None:
    no_symlink(output)
    if scope == "bounded_qualification":
        if output.resolve() != Path(decision["attempt_root"]).resolve():
            raise GateError("qualification_attempt_reuse")
    elif scope == "calendar":
        if output.parent != Path(config["source_root"]) / "windows":
            raise GateError("calendar_window_path_changed")
    else:
        raise GateError("unknown_scope")
    reservation = read(output / "reservation.json")
    if reservation["plan_sha256"] != config["proposal_sha"] or reservation["reserved_rpc"] != 180:
        raise GateError("reservation_binding_changed")
    if scope == "calendar":
        binding, _ = load(bundle / "proposal")
        index = reservation["index"]
        if type(index) is not int or not 0 <= index < 336:
            raise GateError("unknown_window")
        due = binding["windows"][index]["due_ns"]
        if (
            output.name != f"{index:03}"
            or reservation["due_ns"] != due
            or not due <= reservation["started_ns"] <= due + 900 * 10**9
        ):
            raise GateError("calendar_window_binding_changed")


def service_template(bundle: Path, output: Path, receipt: Path) -> None:
    """Review artifact only. Never registers launchd or issues a receipt."""
    data = dict(
        Label="ai.memecoin.f01.prospective01",
        StartInterval=60,
        RunAtLoad=True,
        WorkingDirectory=str(bundle.resolve()),
        ProgramArguments=[
            sys.executable,
            "-B",
            "-m",
            "memecoin_operational.service",
            str(bundle.resolve()),
            "--bundle-sha",
            sha(bundle / "manifest.json"),
            "--receipt",
            str(receipt.resolve()),
            "--receipt-sha",
            "PENDING_FINAL_DECISION",
        ],
        EnvironmentVariables={
            "PYTHONPATH": str((bundle / "runtime").resolve()),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        ProcessType="Background",
        AbandonProcessGroup=False,
        StandardOutPath=str((output.parent / "service.stdout.log").resolve()),
        StandardErrorPath=str((output.parent / "service.stderr.log").resolve()),
    )
    with output.open("xb") as f:
        plistlib.dump(data, f)
