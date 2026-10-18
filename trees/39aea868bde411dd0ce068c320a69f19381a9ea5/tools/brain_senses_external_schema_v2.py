"""Immutable file names and schema identities for external Senses lease v2."""

SCHEMA = "brain-senses-external-v2"
PLAN_FILE = "external-plan-v2.json"
EVENTS_FILE = "events-v2.jsonl"
STOP_FILE = "STOP-v2"
LOCK_FILE = ".writer-v2.lock"

PLAN_SCHEMA = f"{SCHEMA}-plan"
EVENT_SCHEMA = f"{SCHEMA}-event"
BUCKET_SCHEMA = f"{SCHEMA}-bucket"
REQUEST_SCHEMA = f"{SCHEMA}-request"
RECEIPT_SCHEMA = f"{SCHEMA}-receipt"
RESULT_SCHEMA = f"{SCHEMA}-result"
BUNDLE_SCHEMA = f"{SCHEMA}-bundle"
SEAL_SCHEMA = f"{SCHEMA}-seal"
STOP_SCHEMA = f"{SCHEMA}-stop"

MAX_BUCKETS = 13
MAX_REQUESTS = 409
MAX_RESPONSE_BYTES = 32_000_000
RAW_LEASE_BYTES = 3_104_000_000
UTC_DAY_BYTES = 3_300_000_000
UTC_DAY_REQUESTS = 450
MAX_FRAME_WIDTH = 20_000
SELECTION_COUNT = 16
DISK_FLOOR_BYTES = 50 * 2**30
PEAK_WRITE_MARGIN_BYTES = 290_000_000
METADATA_MARGIN_BYTES = 32_000_000
REQUESTS_PER_SECOND = 4

CAPS = {
    "max_requests_total": MAX_REQUESTS,
    "max_requests_per_utc_date": UTC_DAY_REQUESTS,
    "bootstrap_requests": 1,
    "frame_requests": 34,
    "max_frame_width": MAX_FRAME_WIDTH,
    "selection_count": SELECTION_COUNT,
    "max_response_bytes": MAX_RESPONSE_BYTES,
    "bootstrap_raw_bytes": MAX_RESPONSE_BYTES,
    "bootstrap_stored_bytes": MAX_RESPONSE_BYTES,
    "frame_raw_bytes": 256_000_000,
    "frame_stored_bytes": 256_000_000,
    "raw_bytes_total": RAW_LEASE_BYTES,
    "stored_bytes_total": RAW_LEASE_BYTES,
    "raw_bytes_per_utc_date": UTC_DAY_BYTES,
    "stored_bytes_per_utc_date": UTC_DAY_BYTES,
    "disk_floor_bytes": DISK_FLOOR_BYTES,
    "peak_write_margin_bytes": PEAK_WRITE_MARGIN_BYTES,
    "metadata_margin_bytes": METADATA_MARGIN_BYTES,
    "requests_per_second": REQUESTS_PER_SECOND,
    "storage_codec": "raw",
}
