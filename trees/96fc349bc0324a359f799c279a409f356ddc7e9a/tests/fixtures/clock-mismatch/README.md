# Rejected Clock-Mismatch Captures

Unmodified public on-chain captures from prospective campaigns, with exact request/receipt/
response hashes. Both responses were HTTP 200 with no JSON-RPC error.

- Campaign 01, segment 000, attempt 048: context slot 452681508, Clock 452681509.
- Campaign 02, segment 000, attempt 067: context slot 452682339, Clock 452682340.

The strict decoder rejects both. The provider-side cause is unknown. Never normalize these into
matching bank IDs or admit them as valid observations.
