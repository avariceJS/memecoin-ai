"""Stable file/event contract for the finite external Senses writer v1."""

from __future__ import annotations

PLAN_FILE = "external-plan-v1.json"
EVENTS_FILE = "events-v1.jsonl"
LOCK_FILE = ".writer.lock"
STOP_FILE = "STOP"
SCHEMA = "brain-senses-external-v1"
PLAN_SCHEMA = f"{SCHEMA}-plan"
EVENT_SCHEMA = f"{SCHEMA}-event"
BUCKET_SCHEMA = f"{SCHEMA}-bucket"
REQUEST_SCHEMA = f"{SCHEMA}-request"
RECEIPT_SCHEMA = f"{SCHEMA}-receipt"
RESULT_SCHEMA = f"{SCHEMA}-result"
BUNDLE_SCHEMA = f"{SCHEMA}-bundle"
SEAL_SCHEMA = f"{SCHEMA}-seal"
STOP_SCHEMA = f"{SCHEMA}-stop"

# Immutable files below each bucket directory:
#   reservation.json, started.json, requests/00.json,
#   captures/<bucket>-00/response.json[.zst], captures/<bucket>-00/receipt.json,
#   results/00.json, bundle.json, seal.json.
# All JSON is sorted-key compact canonical JSON plus one newline. SHA-256 is
# over canonical bytes; objects with their own hash omit that field.

# Plan exact fields:
#   {schema, schema_version, service_id, provider:'helius-free',
#    endpoint_origin:'https://mainnet.helius-rpc.com' or
#      'https://rpc.helius.xyz',
#    storage:{root_id,volume_uuid,relative_root},
#    code_sha256:{relative source path: sha256},
#    predecessor:{campaign_id,stop_sha256,terminal_receipt_sha256,
#      committed_frontier,gap_intervals,last_inventoried_interval},
#    schedule:{bootstrap:{bucket,start_ns,end_ns},
#      frame:{bucket,start_ns,end_ns},expires_ns},
#    caps:{max_attempts,max_requests_total,max_requests_per_utc_date,
#      bootstrap_requests,frame_requests,max_frame_width,selection_count,
#      max_response_bytes,raw_bytes_total,stored_bytes_total,
#      raw_bytes_per_utc_date,stored_bytes_per_utc_date,disk_floor_bytes,
#      peak_write_margin_bytes,storage_codec}}. No additional keys are accepted.

# Event exact fields:
#   {schema, schema_version, seq, kind, bucket, at_ns, payload,
#    previous_sha256, event_sha256}. Kinds: initialized, gap, reservation,
#    started, bundle, seal, stop. event_sha256 hashes canonical JSON of all
#   fields except event_sha256. First previous_sha256 is 64 ASCII zeroes.

# reservation.json:
#   {schema,schema_version,service_id,bucket,phase,reservation_id,plan_sha256,
#    request_count,raw_byte_cap,stored_byte_cap,created_ns,not_before_ns,
#    expires_ns,predecessor_event_sha256}.
# started.json: {schema,schema_version,reservation_id,plan_sha256,started_ns,pid}.

# Request exact fields: {schema,schema_version,provider,reservation_id,request_id,
#   ordinal,method,params,request_sha256,wire_request_sha256,request_started_ns,decision_ns}.
# request_sha256 is SHA-256 of canonical {method,params}. Its create-only
# publication is the no-replay boundary and must precede the provider call.
# wire_request_sha256 hashes actual Capture.request_json, which must equal
# canonical JSON-RPC {jsonrpc:'2.0',id:1,method,params}.
# Receipt exact fields: {schema,schema_version,provider,request_id,request_sha256,
#   wire_request_sha256,
#   request_started_ns,received_ns,availability_ns:null,decision_ns,
#   response_sha256,response_bytes,storage:{codec,path,sha256,bytes,
#   codec_version}}. codec_version is null for raw bytes.
# Receipt availability_ns is null because receipt fsync is not yet complete.
# Result exact fields: {schema,schema_version,provider,request_id,request_sha256,
#   wire_request_sha256,receipt_sha256,response_sha256,status,result_status,
#   method,event_slot,produced_slots,
#   signatures,block_identity,response_bytes,stored_response_bytes,event_time_ns,
#   availability_ns,decision_ns}. event_time_ns is provider blockTime when
#   supplied; availability_ns is sampled locally after response/receipt and
#   parent directories are fsynced. It is durable local availability, not
#   historical provider availability. decision_ns is local reservation time.
#
# Bundle exact fields: {schema,schema_version,provider,bucket,phase,reservation_id,
#   plan_sha256,status,request_count,requests:[{request_id,request_sha256,
#   receipt_sha256,result_sha256,response_sha256}],raw_bytes,stored_bytes,
#   bootstrap_slot,frame:{start,end_exclusive,anchor_slot,width,
#   inventory_sha256,denominator,selection_count,selected_slots,
#   inclusion_probability,seed,selection_rule},selected_blocks:[{slot,
#   signatures:[...ordered...],blockhash,previous_blockhash,parent_slot}],
#   provider_charge_units:null,provider_charge_status:'unknown_unverified',
#   terminal_status}. For bootstrap,
#   frame is null and selected_blocks is []. Bundle digest is over the complete
#   canonical bundle file. Seal exact fields:
#   {schema,schema_version,bucket,reservation_id,plan_sha256,bundle_sha256,
#   sealed_ns,status}. STOP exact fields:
#   {schema,schema_version,reason,stopped_ns,last_bucket,bundle_sha256,
#   post_stop_requests:0}.
