# Unsupported Config Captures

Original finalized public Solana RPC `getMultipleAccounts` responses. In both, Clock.slot equals
context.slot. The strict decoder rejects a nonzero reserved AmmConfig word at offset 116 with
value 50000; its economic meaning is unknown.

- Token-2022: `work/prospective-public-coherent-01/segments/000/corpus/captures/000`
- Classic: `work/coherence-classic-probe-01/000`

Do not normalize padding, substitute an older config, or infer a fee model. Public on-chain data
only; exact requests, receipts, and raw hashes are preserved. No endpoint key is included.
