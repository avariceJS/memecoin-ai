"""Import bounded JSON-RPC address-signature captures as unknown-support event facts."""

import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from memecoin_population.catalog import entity as checked_entity

from . import observations
from .storage import (
    atomic_json,
    canonical,
    digest,
    file_hash,
    object_path,
    safe_path,
    verify_files,
)

NS = 10**9
MAX_PAGE_BYTES = 4_000_000
MAX_PAGES = 100_000
BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _base58_bytes(value):
    number = 0
    for char in value:
        if char not in BASE58:
            raise ValueError("signature_capture_base58")
        number = number * 58 + BASE58.index(char)
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(value) - len(value.lstrip("1"))) + body


def _capture_root(receipt_path):
    path = Path(receipt_path).resolve()
    root = path.parent
    receipt_bytes = path.read_bytes()
    receipt = json.loads(receipt_bytes)
    required = {
        "version",
        "address",
        "network",
        "method",
        "commitment",
        "page_limit",
        "pages",
        "unique_signatures",
        "aborted",
    }
    if (
        not isinstance(receipt, dict)
        or receipt.get("version")
        not in {
            "alchemy-mcp-signature-cursor-probe-v1",
            "solana-address-signature-capture-v1",
        }
        or not required <= set(receipt)
        or receipt["network"] != "solana-mainnet"
        or receipt["method"] != "getSignaturesForAddress"
        or receipt["commitment"] != "finalized"
        or type(receipt["page_limit"]) is not int
        or not 1 <= receipt["page_limit"] <= 1000
        or not isinstance(receipt["pages"], list)
        or not 1 <= len(receipt["pages"]) <= MAX_PAGES
        or type(receipt["aborted"]) is not bool
        or receipt["aborted"]
        or type(receipt["unique_signatures"]) is not int
        or receipt["unique_signatures"] <= 0
        or type(receipt.get("attempts")) is not int
        or receipt["attempts"] < len(receipt["pages"])
        or type(receipt.get("retries")) is not int
        or receipt["retries"] != 0
        or (
            receipt["version"] == "solana-address-signature-capture-v1"
            and (
                not isinstance(receipt.get("requests"), list)
                or len(receipt["requests"]) != receipt["attempts"]
            )
        )
        or (
            receipt["version"] == "alchemy-mcp-signature-cursor-probe-v1"
            and receipt["attempts"] != len(receipt["pages"])
        )
    ):
        raise ValueError("signature_capture_receipt")
    return root, receipt, digest(receipt), receipt_bytes


def _import_capture(receipt_path, store, *, role, chunk, compare_rows=None, expected_pin=None):
    """Validate capture and optionally compare normalized facts without publishing anything."""
    if role not in {"train", "research_evaluation"}:
        raise ValueError("signature_capture_role")
    root, receipt, receipt_pin, _ = _capture_root(receipt_path)
    raw_address = receipt["address"]
    if not isinstance(raw_address, str) or len(_base58_bytes(raw_address)) != 32:
        raise ValueError("signature_capture_address")
    # Keep the provider identity in the project-wide entity namespace. The raw public
    # address remains in the sealed receipt; the event facts use its stable namespace.
    address = f"solana/address/{raw_address}"
    checked_entity(address)

    with tempfile.TemporaryDirectory(prefix="signature-capture-") as scratch:
        db = sqlite3.connect(Path(scratch) / "facts.sqlite")
        db.execute("pragma cache_size=-8192")
        db.execute("pragma temp_store=FILE")
        db.execute("""create table facts(entity text,kind text,stream text,t integer,id text,
            payload text,primary key(entity,kind,stream,t,id)) without rowid""")
        db.execute("create table seen(signature text primary key,slot integer)")
        page_pins = []
        available_ns = 0
        previous_slot = None
        previous_signature = None
        row_count = 0
        unsupported_time_rows = 0
        expected_before = None
        cursor_chain_verified = True
        previous_received_ns = None
        wire_pins = []
        requests = receipt.get("requests", [])
        try:
            for index, page in enumerate(receipt["pages"]):
                if (
                    not isinstance(page, dict)
                    or type(page.get("request_started_ns")) is not int
                    or type(page.get("received_ns")) is not int
                    or page["received_ns"] < page["request_started_ns"]
                    or type(page.get("request_limit")) is not int
                    or page.get("request_limit") != receipt["page_limit"]
                    or page.get("used_before_cursor") is not (index > 0)
                    or (index == 0 and page.get("before_signature_sha256") is not None)
                    or (
                        index > 0
                        and page.get("before_signature_sha256") is not None
                        and page["before_signature_sha256"]
                        != hashlib.sha256(expected_before.encode()).hexdigest()
                    )
                    or page.get("retried", False) is not False
                    or page.get("local_seal") != "created"
                    or type(page.get("returned")) is not int
                    or page["returned"] <= 0
                    or type(page.get("overlap")) is not int
                    or page["overlap"] != 0
                    or type(page.get("finalized")) is not bool
                    or type(page.get("monotonic")) is not bool
                    or (
                        previous_received_ns is not None
                        and page["request_started_ns"] < previous_received_ns
                    )
                ):
                    raise ValueError("signature_capture_page_receipt")
                if "before_signature_sha256" not in page or (
                    index > 0 and page.get("before_signature_sha256") is None
                ):
                    cursor_chain_verified = False
                if receipt["version"] == "solana-address-signature-capture-v1":
                    request_record = requests[index]
                    request_id = f"brain-senses-{index:04d}"
                    request_params = {"limit": receipt["page_limit"], "commitment": "finalized"}
                    if index > 0:
                        request_params["before"] = expected_before
                    expected_request_sha = hashlib.sha256(
                        canonical(
                            dict(
                                jsonrpc="2.0",
                                id=request_id,
                                method="getSignaturesForAddress",
                                params=[raw_address, request_params],
                            )
                        )
                    ).hexdigest()
                    if (
                        request_record.get("request_id") != request_id
                        or page.get("request_id") != request_id
                        or request_record.get("method") != receipt["method"]
                        or request_record.get("commitment") != receipt["commitment"]
                        or request_record.get("limit") != receipt["page_limit"]
                        or (
                            "address_sha256" in request_record
                            and request_record["address_sha256"]
                            != hashlib.sha256(raw_address.encode()).hexdigest()
                        )
                        or request_record.get("request_sha256") != expected_request_sha
                        or request_record.get("before_signature_sha256")
                        != page.get("before_signature_sha256")
                        or request_record.get("request_started_ns") != page["request_started_ns"]
                        or request_record.get("received_ns") != page["received_ns"]
                        or request_record.get("result_status") != "page"
                        or request_record.get("wire_path") != page.get("wire_path")
                        or request_record.get("wire_sha256") != page.get("wire_sha256")
                        or request_record.get("wire_bytes") != page.get("wire_bytes")
                        or request_record.get(
                            "wire_complete", request_record.get("wire_bytes", 0) < MAX_PAGE_BYTES
                        )
                        is not True
                    ):
                        raise ValueError("signature_capture_request_binding")
                relative = page.get("path")
                if relative != f"pages/page-{index:04d}.json":
                    raise ValueError("signature_capture_page_path")
                path = (root / relative).resolve()
                if not path.is_relative_to(root) or path.is_symlink():
                    raise ValueError("signature_capture_page_location")
                raw = path.read_bytes()
                if (
                    len(raw) > MAX_PAGE_BYTES
                    or len(raw) != page.get("response_bytes")
                    or hashlib.sha256(raw).hexdigest() != page.get("response_sha256")
                ):
                    raise ValueError("signature_capture_page_checksum")
                rows = json.loads(raw)
                if (
                    not isinstance(rows, list)
                    or not 1 <= len(rows) <= receipt["page_limit"]
                    or len(rows) != page.get("returned")
                    or page["finalized"] is not True
                    or page["monotonic"] is not True
                ):
                    raise ValueError("signature_capture_page_shape")
                previous_received_ns = page["received_ns"]
                page_pin = hashlib.sha256(raw).hexdigest()
                page_pins.append(page_pin)
                wire_pin = None
                if receipt["version"] == "solana-address-signature-capture-v1":
                    wire_relative = page.get("wire_path")
                    if wire_relative != f"wire/response-{index:04d}.bin":
                        raise ValueError("signature_capture_wire_path")
                    wire_path = (root / wire_relative).resolve()
                    if not wire_path.is_relative_to(root) or wire_path.is_symlink():
                        raise ValueError("signature_capture_wire_location")
                    wire = wire_path.read_bytes()
                    if (
                        len(wire) > MAX_PAGE_BYTES
                        or len(wire) != page.get("wire_bytes")
                        or hashlib.sha256(wire).hexdigest() != page.get("wire_sha256")
                        or page.get("http_status") != 200
                        or page.get("response_id") != page.get("request_id")
                        or page.get("wire_complete", page.get("wire_bytes", 0) < MAX_PAGE_BYTES)
                        is not True
                    ):
                        raise ValueError("signature_capture_wire_checksum")
                    envelope = json.loads(wire)
                    if (
                        not isinstance(envelope, dict)
                        or envelope.get("jsonrpc") != "2.0"
                        or envelope.get("id") != page["request_id"]
                        or envelope.get("result") != rows
                        or "error" in envelope
                    ):
                        raise ValueError("signature_capture_wire_result")
                    wire_pin = hashlib.sha256(wire).hexdigest()
                    wire_pins.append(wire_pin)
                available_ns = max(available_ns, page["received_ns"])
                page_slots = []
                for row in rows:
                    if (
                        not isinstance(row, dict)
                        or not isinstance(row.get("signature"), str)
                        or not row["signature"]
                        or len(_base58_bytes(row["signature"])) != 64
                        or type(row.get("slot")) is not int
                        or row["slot"] < 0
                        or row.get("confirmationStatus") != "finalized"
                        or "err" not in row
                        or (row["err"] is not None and not isinstance(row["err"], dict))
                    ):
                        raise ValueError("signature_capture_record")
                    duplicate = db.execute(
                        "select 1 from seen where signature=?", (row["signature"],)
                    ).fetchone()
                    if duplicate:
                        raise ValueError("signature_capture_duplicate")
                    if previous_signature == row["signature"]:
                        raise ValueError("signature_capture_cursor_overlap")
                    if previous_slot is not None and row["slot"] > previous_slot:
                        raise ValueError("signature_capture_slot_order")
                    db.execute("insert into seen values(?,?)", (row["signature"], row["slot"]))
                    page_slots.append(row["slot"])
                    previous_slot = row["slot"]
                    previous_signature = row["signature"]
                    expected_before = row["signature"]
                    block_time = row.get("blockTime")
                    if block_time is None:
                        unsupported_time_rows += 1
                        continue
                    if type(block_time) is not int or block_time < 0:
                        raise ValueError("signature_capture_block_time")
                    event_ns = block_time * NS
                    if event_ns > page["received_ns"]:
                        raise ValueError("signature_capture_event_after_receipt")
                    succeeded = row.get("err") is None
                    fact = dict(
                        kind="event",
                        entity=address,
                        stream="solana/address-signatures",
                        id=row["signature"],
                        event_ns=event_ns,
                        available_ns=page["received_ns"],
                        evidence=sorted([receipt_pin, page_pin] + ([wire_pin] if wire_pin else [])),
                        values=[float(row["slot"]), float(succeeded)],
                    )
                    db.execute(
                        "insert into facts values(?,?,?,?,?,?)",
                        (
                            address,
                            "event",
                            fact["stream"],
                            event_ns,
                            row["signature"],
                            canonical(fact).decode(),
                        ),
                    )
                    row_count += 1
                if page_slots and (
                    min(page_slots) != page.get("oldest_slot")
                    or max(page_slots) != page.get("newest_slot")
                ):
                    raise ValueError("signature_capture_slot_summary")
            seen_count = db.execute("select count(*) from seen").fetchone()[0]
            if seen_count != receipt["unique_signatures"]:
                raise ValueError("signature_capture_accounting")
            db.commit()

            importer_sha = file_hash(Path(__file__))
            capture_set_pin = digest(
                dict(receipt=receipt_pin, pages=page_pins, wire_pages=wire_pins)
            )
            accounting = dict(
                captured_signature_rows=receipt["unique_signatures"],
                event_time_resolved=row_count,
                unsupported_missing_block_time=unsupported_time_rows,
                support_status="unknown",
                coverage_complete=False,
                absence_inference_permitted=False,
                cursor_chain_verified=cursor_chain_verified,
            )
            import_manifest = dict(
                version="solana-signature-import-v1",
                receipt_pin=receipt_pin,
                capture_set_pin=capture_set_pin,
                importer_sha=importer_sha,
                entity=address,
                role=role,
                available_ns=available_ns,
                accounting=accounting,
            )
            import_pin = digest(import_manifest)

            def rows():
                for (payload,) in db.execute(
                    "select payload from facts order by entity,kind,stream,t,id"
                ):
                    yield json.loads(payload)

            if compare_rows is not None:
                expected = iter(compare_rows)
                compared = 0
                for actual in rows():
                    try:
                        wanted = next(expected)
                    except StopIteration:
                        raise ValueError("qualification_observed_values_fact_count") from None
                    if actual != wanted:
                        raise ValueError("qualification_observed_values_fact_mismatch")
                    compared += 1
                try:
                    next(expected)
                except StopIteration:
                    pass
                else:
                    raise ValueError("qualification_observed_values_fact_count")
                if compared != row_count or expected_pin is None:
                    raise ValueError("qualification_observed_values_fact_count")
                return dict(
                    observation_pin=expected_pin,
                    import_pin=import_pin,
                    accounting=accounting,
                    receipt_pin=receipt_pin,
                )

            store_path = safe_path(store)
            import_root = safe_path(store_path / "capture-imports")
            import_root.mkdir(parents=True, exist_ok=True)
            atomic_json(import_root / f"{import_pin}.json", import_manifest)

            if row_count == 0:
                return dict(
                    observation_pin=None,
                    import_pin=import_pin,
                    accounting=accounting,
                    receipt_pin=receipt_pin,
                )

            schema = dict(
                version="market-observations-v1",
                role=role,
                availability="point_in_time",
                streams={
                    "solana/address-signatures": dict(
                        process="solana-address-signature-references-v1",
                        features=[
                            dict(name="slot", unit="solana_slot", modality="chain_activity"),
                            dict(
                                name="transaction_rpc_succeeded",
                                unit="boolean_indicator",
                                modality="chain_activity",
                            ),
                        ],
                    )
                },
            )
            observation_pin = observations.publish(
                store,
                rows(),
                schema=schema,
                cutoff=available_ns,
                sources=[dict(kind="solana-signature-import", pin=import_pin)],
                chunk=chunk,
            )
            return dict(
                observation_pin=observation_pin,
                import_pin=import_pin,
                accounting=accounting,
                receipt_pin=receipt_pin,
            )
        finally:
            db.close()


def publish(receipt_path, store, *, role="train", chunk=4096):
    """Seal provider-returned signature facts; never infer complete coverage from these pages."""
    return _import_capture(receipt_path, store, role=role, chunk=chunk)


def _signature_process_contract(store, *, publish=False):
    from . import qualification

    schema = dict(
        version="payload-schema-v1",
        fields=[
            dict(name="slot", unit="solana_slot", denomination="integer chain slot"),
            dict(
                name="transaction_rpc_succeeded",
                unit="boolean_indicator",
                denomination="1 means RPC err is null; 0 means RPC err is non-null",
            ),
        ],
    )
    schema_pin = qualification.digest(schema)
    schema_path = safe_path(store) / "qualification-payload-schemas" / f"{schema_pin}.json"
    if publish:
        qualification.publish_payload_schema(store, schema)
    elif not schema_path.is_file() or json.loads(schema_path.read_text()) != schema:
        raise ValueError("qualification_observed_values_schema_dependency")
    definitions = {
        "event_identity_rule": "key-v1",
        "event_time_basis": "block-time-v1",
        "interval_convention": "half-open-start-inclusive-end-exclusive-v1",
        "revision_policy": "append-revisions-v1",
    }
    rules = {}
    for name, definition in definitions.items():
        rule_value = dict(version=qualification.RULES[name], definition=definition)
        rule_pin = qualification.digest(rule_value)
        rule_path = safe_path(store) / "qualification-rules" / f"{rule_pin}.json"
        if publish:
            qualification.publish_rule(store, name, definition)
        elif not rule_path.is_file() or json.loads(rule_path.read_text()) != rule_value:
            raise ValueError("qualification_observed_values_rule_dependency")
        rules[name] = dict(version=qualification.RULES[name], pin=rule_pin)
    process = dict(
        version="process-contract-v1",
        name="solana-address-signature-references-v1",
        stream="solana/address-signatures",
        semantics="provider-returned finalized signature references, not decoded market events",
        unit="provider_signature_rows",
        source_kind="solana_address_signature_capture_v1",
        entity_namespace="solana/address",
        modality="chain_activity",
        payload_schema_pin=schema_pin,
        event_identity_rule=rules["event_identity_rule"],
        event_time_basis=rules["event_time_basis"],
        timestamp_precision_ns=NS,
        interval_convention=rules["interval_convention"],
        revision_policy=rules["revision_policy"],
    )
    return process, qualification.process_contract_pin(process, store=store)


def _verify_observed_values_capture(value, *, store, source_store, capture_root):
    """Re-run raw capture import and verify every normalized event binds to its pinned source."""
    from . import qualification

    root = Path(capture_root).resolve()
    receipt_path = root / "receipt.json" if root.is_dir() else root
    raw_root, capture, capture_receipt_pin, _ = _capture_root(receipt_path)
    address = capture.get("address")
    if not isinstance(address, str) or len(_base58_bytes(address)) != 32:
        raise ValueError("signature_capture_address")
    entity = f"solana/address/{address}"
    checked_entity(entity)
    if (
        capture_receipt_pin != value["capture_receipt_pin"]
        or entity != value["entity"]
        or value["verifier_id"] != "solana_address_signatures_v1"
    ):
        raise ValueError("qualification_observed_values_capture_binding")
    import_path = safe_path(source_store) / "capture-imports" / f"{value['import_pin']}.json"
    import_manifest = json.loads(import_path.read_text())
    if digest(import_manifest) != value["import_pin"]:
        raise ValueError("qualification_observed_values_import_checksum")
    if (
        import_manifest.get("version") != "solana-signature-import-v1"
        or import_manifest.get("receipt_pin") != capture_receipt_pin
        or import_manifest.get("capture_set_pin") != value["capture_set_pin"]
        or import_manifest.get("importer_sha") != value["decoder_pin"]
        or import_manifest.get("importer_sha") != file_hash(Path(__file__))
        or import_manifest.get("entity") != entity
        or import_manifest.get("role") != value["role"]
        or import_manifest.get("available_ns") != value["available_ns"]
    ):
        raise ValueError("qualification_observed_values_import_binding")
    process = value["process_contract"]
    process_pin = value["process_contract_pin"]
    expected_process, expected_process_pin = _signature_process_contract(store)
    source = observations.Source(source_store, value["source_pin"], role=value["role"])
    stream_schema = source.schema["streams"].get(process["stream"])
    payload_schema = json.loads(
        (
            safe_path(store)
            / "qualification-payload-schemas"
            / f"{process['payload_schema_pin']}.json"
        ).read_text()
    )
    expected_features = [
        dict(name=field["name"], unit=field["unit"], modality=process["modality"])
        for field in payload_schema["fields"]
    ]
    if (
        process != expected_process
        or process_pin != expected_process_pin
        or process["source_kind"] != "solana_address_signature_capture_v1"
        or set(source.schema["streams"]) != {process["stream"]}
        or stream_schema != dict(process=process["name"], features=expected_features)
        or source.schema["availability"] != "point_in_time"
        or import_manifest["accounting"]["support_status"] != "unknown"
        or import_manifest["accounting"]["coverage_complete"] is not False
        or import_manifest["accounting"]["absence_inference_permitted"] is not False
    ):
        raise ValueError("qualification_observed_values_process_binding")
    chunk = source.manifest["binding"].get("chunk")
    if type(chunk) is not int or not 1 <= chunk <= 4096:
        raise ValueError("qualification_observed_values_source_chunk")
    # Normalize to a disk-backed scratch DB and compare row-for-row with existing source facts.
    # No observation publication or import-manifest write occurs during verification.
    imported = _import_capture(
        receipt_path,
        source_store,
        role=value["role"],
        chunk=chunk,
        compare_rows=source.rows(),
        expected_pin=value["source_pin"],
    )
    if (
        imported["observation_pin"] != value["source_pin"]
        or imported["import_pin"] != value["import_pin"]
        or imported["receipt_pin"] != value["capture_receipt_pin"]
        or imported["accounting"]["captured_signature_rows"] != value["accounting"]["captured"]
        or imported["accounting"]["event_time_resolved"] != value["accounting"]["observed"]
        or imported["accounting"]["unsupported_missing_block_time"]
        != value["accounting"]["unresolved"]
    ):
        raise ValueError("qualification_observed_values_reimport_mismatch")
    count = 0
    for row in source.rows():
        if row["kind"] != "event" or row["entity"] != entity or row["stream"] != process["stream"]:
            raise ValueError("qualification_observed_values_fact_kind")
        if (
            row["event_ns"] > row["available_ns"]
            or row["available_ns"] > value["available_ns"]
            or len(row["values"]) != len(payload_schema["fields"])
            or type(row["values"][0]) not in (int, float)
            or row["values"][0] < 0
            or row["values"][1] not in (0.0, 1.0)
            or capture_receipt_pin not in row["evidence"]
        ):
            raise ValueError("qualification_observed_values_fact_shape")
        count += 1
    if count != value["accounting"]["observed"]:
        raise ValueError("qualification_observed_values_fact_count")
    return dict(
        verifier_id=value["verifier_id"],
        verifier_code_pin=qualification._observed_values_code_pin(value["verifier_id"]),
        source_pin=value["source_pin"],
        import_pin=value["import_pin"],
        capture_set_pin=value["capture_set_pin"],
        capture_receipt_pin=capture_receipt_pin,
        decoder_pin=value["decoder_pin"],
        process_contract_pin=process_pin,
        entity=entity,
        role=value["role"],
        available_ns=value["available_ns"],
        accounting=value["accounting"],
    )


def publish_verified_values(receipt_path, store, *, role="train", output_store=None, chunk=4096):
    """Publish only verifier-backed positive event facts; no enrollment or support is created."""
    from . import qualification

    if role not in {"train", "research_evaluation"}:
        raise ValueError("signature_capture_role")
    if output_store is not None and safe_path(output_store) != safe_path(store):
        raise ValueError("signature_capture_output_store_must_match")
    root, capture, capture_receipt_pin, _ = _capture_root(receipt_path)
    raw_address = capture.get("address")
    if not isinstance(raw_address, str) or len(_base58_bytes(raw_address)) != 32:
        raise ValueError("signature_capture_address")
    address = f"solana/address/{raw_address}"
    checked_entity(address)  # protected identities are rejected before any page body is opened
    imported = publish(receipt_path, store, role=role, chunk=chunk)
    if imported["observation_pin"] is None:
        return dict(**imported, verified_observation_pin=None, qualification_receipt_pin=None)
    process, process_pin = _signature_process_contract(store, publish=True)
    import_manifest = json.loads(
        (safe_path(store) / "capture-imports" / f"{imported['import_pin']}.json").read_text()
    )
    accounting = dict(
        captured=imported["accounting"]["captured_signature_rows"],
        observed=imported["accounting"]["event_time_resolved"],
        unresolved=imported["accounting"]["unsupported_missing_block_time"],
    )
    qualification_code_pin = qualification._observed_values_code_pin("solana_address_signatures_v1")
    payload_schema_pin = process["payload_schema_pin"]
    input_pins = sorted(
        {
            imported["observation_pin"],
            imported["import_pin"],
            import_manifest["capture_set_pin"],
            capture_receipt_pin,
            import_manifest["importer_sha"],
            process_pin,
            payload_schema_pin,
            qualification_code_pin,
            *(process[name]["pin"] for name in qualification.RULES),
        }
    )
    receipt = dict(
        version="qualification-observed-values-v2",
        process_contract=process,
        process_contract_pin=process_pin,
        entity=address,
        source_pin=imported["observation_pin"],
        import_pin=imported["import_pin"],
        capture_set_pin=import_manifest["capture_set_pin"],
        capture_receipt_pin=capture_receipt_pin,
        decoder_pin=import_manifest["importer_sha"],
        verifier_id="solana_address_signatures_v1",
        qualification_code_pin=qualification_code_pin,
        inputs=input_pins,
        role=role,
        available_ns=import_manifest["available_ns"],
        accounting_unit="provider_signature_rows",
        accounting=accounting,
        capabilities=[qualification.OBSERVED_VALUE_CAPABILITY],
        verification=dict(
            verifier_id="solana_address_signatures_v1",
            verifier_code_pin=qualification_code_pin,
            input_pin=imported["import_pin"],
            process_contract_pin=process_pin,
            result_pin="0" * 64,
        ),
    )
    qualification_receipt_pin = qualification.publish_observed_values_receipt(
        store, receipt, source_store=store, capture_root=root
    )
    source = observations.Source(store, imported["observation_pin"], role=role)

    def verified_rows():
        for row in source.rows():
            if row["kind"] != "event":
                raise ValueError("signature_capture_verified_values_kind")
            yield dict(
                row,
                evidence=sorted(set([*row["evidence"], qualification_receipt_pin])),
            )

    verified_pin = observations.publish(
        output_store or store,
        verified_rows(),
        schema=source.schema,
        cutoff=source.manifest["binding"]["cutoff_ns"],
        sources=[
            dict(kind="observed-values-v2", pin=qualification_receipt_pin),
            dict(kind="raw-observation-source", pin=imported["observation_pin"]),
        ],
        chunk=chunk,
    )
    return dict(
        **imported,
        verified_observation_pin=verified_pin,
        qualification_receipt_pin=qualification_receipt_pin,
    )


def _copy_json_pin(source, target, directory, pin):
    source_path = safe_path(source) / directory / f"{pin}.json"
    value = json.loads(source_path.read_text())
    if digest(value) != pin:
        raise ValueError("signature_capture_replica_dependency_checksum")
    target_path = safe_path(target) / directory / f"{pin}.json"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(target_path, value)


def _copy_cas_object(source, target, pin):
    origin = object_path(source, pin)
    verify_files(origin, pin)
    destination = object_path(target, pin)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        verify_files(destination, pin)
        return
    shutil.copytree(origin, destination)
    verify_files(destination, pin)


def _copy_file_idempotent(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != source.read_bytes():
            raise ValueError("signature_capture_replica_immutable_conflict")
        return
    shutil.copyfile(source, target)


def _copy_observation_source(source, target, pin, role):
    manifest_path = safe_path(source) / "observation-sources" / f"{pin}.json"
    manifest = json.loads(manifest_path.read_text())
    if digest(manifest) != pin:
        raise ValueError("signature_capture_replica_observation_checksum")
    source_obj = observations.Source(source, pin, role=role)
    for part in source_obj.parts:
        _copy_cas_object(source, target, part["pin"])
    # Paged catalogs use their own immutable CAS objects, separate from fact shards.
    if hasattr(source_obj.parts, "pages"):
        for page in source_obj.parts.pages:
            _copy_cas_object(source, target, page["pin"])
    target_dir = safe_path(target) / "observation-sources"
    target_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(target_dir / f"{pin}.json", manifest)
    return observations.Source(target, pin, role=role)


def replicate_verified_values(
    source_store,
    target_store,
    qualification_receipt_pin,
    verified_observation_pin,
    *,
    capture_root,
):
    """Copy and revalidate one capture partition's complete observed-values dependency bundle."""
    from . import qualification

    source = safe_path(source_store)
    target = safe_path(target_store)
    if source == target:
        raise ValueError("signature_capture_replica_distinct_stores")
    value = qualification.load_observed_values_receipt(
        source,
        qualification_receipt_pin,
        source_store=source,
        capture_root=capture_root,
    )
    role = value["role"]
    raw_source = observations.Source(source, value["source_pin"], role=role)
    verified_source = observations.Source(source, verified_observation_pin, role=role)
    output_binding = verified_source.manifest["binding"]
    if output_binding["sources"] != [
        {"kind": "observed-values-v2", "pin": qualification_receipt_pin},
        {"kind": "raw-observation-source", "pin": value["source_pin"]},
    ]:
        raise ValueError("signature_capture_replica_output_binding")
    output_count = 0
    raw_rows = iter(raw_source.rows())
    for row in verified_source.rows():
        try:
            raw_row = next(raw_rows)
        except StopIteration:
            raise ValueError("signature_capture_replica_output_count") from None
        if (
            row["kind"] != "event"
            or row["entity"] != value["entity"]
            or row["stream"] != value["process_contract"]["stream"]
            or qualification_receipt_pin not in row["evidence"]
            or row
            != dict(
                raw_row,
                evidence=sorted(set([*raw_row["evidence"], qualification_receipt_pin])),
            )
        ):
            raise ValueError("signature_capture_replica_output_fact")
        output_count += 1
    try:
        next(raw_rows)
    except StopIteration:
        pass
    else:
        raise ValueError("signature_capture_replica_output_count")
    if output_count != value["accounting"]["observed"]:
        raise ValueError("signature_capture_replica_output_count")

    root = Path(capture_root).resolve()
    receipt_path = root / "receipt.json" if root.is_dir() else root
    raw_root, capture, capture_pin, _ = _capture_root(receipt_path)
    if capture_pin != value["capture_receipt_pin"]:
        raise ValueError("signature_capture_replica_capture_pin")
    import_manifest = json.loads(
        (source / "capture-imports" / f"{value['import_pin']}.json").read_text()
    )
    if digest(import_manifest) != value["import_pin"]:
        raise ValueError("signature_capture_replica_import_checksum")

    target.mkdir(parents=True, exist_ok=True)
    _copy_observation_source(source, target, value["source_pin"], role)
    _copy_observation_source(source, target, verified_observation_pin, role)
    _copy_json_pin(source, target, "capture-imports", value["import_pin"])
    _copy_json_pin(
        source,
        target,
        "qualification-observed-values-v2",
        qualification_receipt_pin,
    )
    process = value["process_contract"]
    _copy_json_pin(
        source,
        target,
        "qualification-payload-schemas",
        process["payload_schema_pin"],
    )
    for rule_name in qualification.RULES:
        _copy_json_pin(
            source,
            target,
            "qualification-rules",
            process[rule_name]["pin"],
        )

    capture_bundle = target / "qualification-captures" / value["capture_set_pin"]
    capture_bundle.mkdir(parents=True, exist_ok=True)
    files = ["receipt.json"]
    for page in capture["pages"]:
        files.append(page["path"])
        if page.get("wire_path"):
            files.append(page["wire_path"])
    for relative in files:
        source_file = raw_root / relative
        if source_file.is_symlink() or not source_file.is_file():
            raise ValueError("signature_capture_replica_capture_file")
        destination = capture_bundle / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_file_idempotent(source_file, destination)
    packaged_receipt = capture_bundle / "receipt.json"
    packaged_root, _, packaged_pin, _ = _capture_root(packaged_receipt)
    if packaged_pin != value["capture_receipt_pin"]:
        raise ValueError("signature_capture_replica_capture_copy")

    qualification.load_observed_values_receipt(
        target,
        qualification_receipt_pin,
        source_store=target,
        capture_root=packaged_root,
    )
    copied_output = observations.Source(target, verified_observation_pin, role=role)
    if sum(1 for _ in copied_output.rows()) != output_count:
        raise ValueError("signature_capture_replica_output_copy")
    return dict(
        qualification_receipt_pin=qualification_receipt_pin,
        source_pin=value["source_pin"],
        verified_observation_pin=verified_observation_pin,
        capture_root=str(packaged_root),
        observed=output_count,
    )
