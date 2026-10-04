"""Single bounded Alchemy indexed probe, isolated from frozen source allowlists."""

import json
import os
import time
from pathlib import Path

from memecoin_ai.domain import canonical
from memecoin_ai.sources import Capture, HTTPSourceError, ReadOnlyRPC, TransportSourceError
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import Ledger, publish

from .ranges import append_page, progress


class IndexedRPC(ReadOnlyRPC):
    def fetch(self, method, params):
        if method == "getTransaction":
            return super().fetch(method, params)
        if method != "getTransactionsForAddress":
            raise ValueError("indexed_read_only_method")
        request = canonical(dict(jsonrpc="2.0", id=1, method=method, params=params))
        start = self.clock()
        body = self.transport(self._endpoint, request.encode(), self.timeout, self.max_bytes)
        end = self.clock()
        if end < start:
            raise ValueError("clock_regression")
        return Capture(self.source_alias, method, request, start, end, body)


def run(root, client, *, pacing=1):
    binding = read(root / "binding.json")
    (root / "attempts").mkdir(exist_ok=False)
    start = time.monotonic()

    def call(method, params):
        paths = sorted((root / "attempts").iterdir())
        cost = 100 if method == "getTransactionsForAddress" else 40
        used = sum(read(p / "reservation.json")["cu"] for p in paths)
        if len(paths) >= 5 or used + cost > 440 or time.monotonic() - start > 108:
            raise ValueError("probe_bound")
        if paths:
            time.sleep(
                max(
                    0,
                    pacing
                    - (time.time_ns() - read(paths[-1] / "reservation.json")["started_ns"]) / 1e9,
                )
            )
        p = root / "attempts" / f"{len(paths):06d}"
        p.mkdir()
        publish(p / "reservation.json", dict(method=method, cu=cost, started_ns=time.time_ns()))
        cap = client.fetch(method, params)
        cap.save(p / "capture")
        # New lane explicitly flushes payload and metadata before publishing availability.
        for f in (p / "capture").iterdir():
            with f.open("rb") as stream:
                os.fsync(stream.fileno())
        fd = os.open(p / "capture", os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        publish(p / "availability.json", dict(available_ns=time.time_ns()))
        seal(p)
        return cap.result(), p

    try:
        pages = []
        first = None
        for pool in binding["cohort"]:
            with Ledger(
                root / ("range-" + pool),
                dict(pool=pool, max_pages=2, anchor_signature=binding["anchors"][pool]),
            ) as ledger:
                cursor = None
                for _ in range(2):
                    cfg = dict(
                        transactionDetails="full",
                        commitment="finalized",
                        encoding="jsonParsed",
                        sortOrder="desc",
                        limit=20,
                    )
                    if cursor is not None:
                        cfg["paginationToken"] = cursor
                    value, p = call("getTransactionsForAddress", [pool, cfg])
                    if (
                        not isinstance(value, dict)
                        or not isinstance(value.get("transactions"), list)
                        or len(value["transactions"]) > 20
                    ):
                        raise ValueError("indexed_response_schema")
                    rows = value["transactions"]
                    next_cursor = value.get("paginationToken")
                    for tx in rows:
                        keys = tx["transaction"]["message"]["accountKeys"]
                        if pool not in [k["pubkey"] if isinstance(k, dict) else k for k in keys]:
                            raise ValueError("pool_reference_mismatch")
                    append_page(ledger, p, rows, cursor, next_cursor)
                    if first is None and pool == binding["cohort"][0] and rows:
                        first = rows[0]
                    pages.append(
                        dict(
                            pool=pool,
                            path=str(p.resolve()),
                            returned=len(rows),
                            progress=progress(ledger),
                        )
                    )
                    cursor = next_cursor
                    if cursor is None:
                        break
        standard = None
        if first:
            value, p = call(
                "getTransaction",
                [
                    first["transaction"]["signatures"][0],
                    dict(
                        commitment="finalized",
                        encoding="jsonParsed",
                        maxSupportedTransactionVersion=0,
                    ),
                ],
            )
            standard = dict(
                path=str(p.resolve()),
                signature=first["transaction"]["signatures"][0],
                core_equal=all(
                    value.get(k) == first.get(k)
                    for k in ["slot", "blockTime", "transaction", "meta", "version"]
                )
                if isinstance(value, dict)
                else False,
            )
        result = dict(
            pages=pages,
            standard=standard,
            nominal_cu=sum(
                read(p / "reservation.json")["cu"] for p in (root / "attempts").iterdir()
            ),
            attempts=len(list((root / "attempts").iterdir())),
            history_complete=False,
            training_eligible=False,
        )
        publish(root / "result.json", result)
        seal(root)
        return result
    except Exception as exc:
        category = (
            "http"
            if isinstance(exc, HTTPSourceError)
            else "transport_" + exc.reason
            if isinstance(exc, TransportSourceError)
            else "source_or_integrity"
        )
        failure = dict(
            category=category,
            error_type=type(exc).__name__,
            http_status=getattr(exc, "status", None),
            no_retry=True,
        )
        publish(root / "STOP", failure)
        raise RuntimeError(category) from None
    finally:
        client.close()


if __name__ == "__main__":
    import sys

    from memecoin_collector.qualification import endpoint, identity
    from memecoin_operational.worker import lease_watchdog

    root = Path(sys.argv[1])
    lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]))
    try:
        b = read(root / "binding.json")
        if time.time_ns() > b["expires_ns"]:
            raise ValueError("expired")
        for path, pin in b["code"].items():
            if sha(Path(path)) != pin:
                raise ValueError("code_binding")
        url = endpoint(Path(b["settings"]))
        if identity(url) != b["credential_sha"]:
            raise ValueError("credential_binding")
        r = run(
            root,
            IndexedRPC(
                url, "brain-b06-alchemy", timeout=10, max_bytes=2 * 1024 * 1024, keep_alive=True
            ),
        )
        print(
            json.dumps(
                dict(status="completed", attempts=r["attempts"], nominal_cu=r["nominal_cu"])
            ),
            flush=True,
        )
    except Exception as exc:
        print(
            json.dumps(
                dict(
                    status="failed", category="bounded_indexed_probe", error_type=type(exc).__name__
                )
            ),
            flush=True,
        )
        raise SystemExit(1) from None
