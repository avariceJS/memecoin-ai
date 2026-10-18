"""Reproduce B06 interpretation of pinned evidence, without network or retroactive admission."""

import json
from pathlib import Path

from memecoin_ai.sources import Capture
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import digest, seal, verify
from memecoin_runner.ledger import publish

from .decoder import decode
from .schema import SCHEMA

B05_PIN = "088d61f3ec317f13cdffa61451f3dd95f5372c07e0da8b4f273bdc6347eeb9f4"


def reproduce(b05: Path, indexed: Path, indexed_pin: str, official: Path) -> dict:
    for root, pin in [(b05, B05_PIN), (indexed, indexed_pin)]:
        if sha(root / "manifest.json") != pin:
            raise ValueError("external_pin_mismatch")
        verify(root)
    if sha(official / "pump_amm.json") != SCHEMA["provenance"]["sha256"]:
        raise ValueError("IDL_pin_mismatch")
    original_idl = read(official / "pump_amm.json")
    for t in original_idl["types"]:
        if t["name"] in SCHEMA["fields"]:
            assert [(f["name"], f["type"]) for f in t["type"]["fields"]] == SCHEMA["fields"][
                t["name"]
            ]
    observations = []
    for item in read(b05 / "result.json")["transactions"]:
        evidence = item["evidence"]
        path = Path(evidence["raw_path"])
        if sha(path / "manifest.json") != evidence["manifest_sha"]:
            raise ValueError("raw_pin_mismatch")
        verify(path)
        cap = Capture.load(path / "capture")
        result = decode(cap.result())
        observations.append(
            dict(
                pool_selected=item["pool"],
                raw_path=str(path),
                raw_manifest_sha=evidence["manifest_sha"],
                original_available_ns=evidence["available_ns"],
                receipt_ns=cap.received_ns,
                signature=evidence["identity"],
                slot=evidence["slot"],
                event_time_s=evidence["event_time_s"],
                interpretation=result,
                decoded_semantic_sha=digest(result),
                historical_online_admission=False,
            )
        )
    attempts = []
    for p in sorted((indexed / "attempts").iterdir()):
        verify(p)
        cap = Capture.load(p / "capture")
        q = json.loads(cap.request_json)
        r = json.loads(cap.response)
        reservation = read(p / "reservation.json")
        available = read(p / "availability.json")["available_ns"]
        if not reservation["started_ns"] <= cap.request_started_ns <= cap.received_ns <= available:
            raise ValueError("clock_integrity")
        attempts.append(
            dict(
                method=cap.method,
                request_config=q["params"][1],
                nominal_CU_reserved=reservation["cu"],
                rpc_error_code=r.get("error", {}).get("code"),
                result_present="result" in r,
                raw_manifest_sha=sha(p / "manifest.json"),
            )
        )
    return dict(
        observations=observations,
        indexed_attempts=attempts,
        indexed_pin=indexed_pin,
        indexed_stopped=(indexed / "STOP").exists(),
        new_network_requests=0,
        fits=0,
        execution_model="none",
        independent_provider_completeness_verified=False,
        IDL_provenance=SCHEMA["provenance"],
    )


def snapshot(b05, indexed, indexed_pin, official, output):
    import time

    report = reproduce(b05, indexed, indexed_pin, official)
    output.mkdir(exist_ok=False)
    publish(output / "report.json", report)
    publish(
        output / "availability.json",
        dict(interpretation_available_ns=time.time_ns(), backdated=False),
    )
    seal(output)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("b05", type=Path)
    p.add_argument("indexed", type=Path)
    p.add_argument("indexed_pin")
    p.add_argument("official", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    snapshot(a.b05, a.indexed, a.indexed_pin, a.official, a.output)
