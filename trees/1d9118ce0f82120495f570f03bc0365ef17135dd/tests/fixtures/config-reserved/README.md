# Real unsupported config captures, 2026-10-02

Original Solana public RPC getMultipleAccounts responses, HTTP 200 / finalized.
Clock.slot equals context.slot in both. Existing strict decoder rejects nonzero
reserved AmmConfig word at offset 116, value 50000; economic meaning unknown.

- token2022: work/prospective-public-coherent-01/segments/000/corpus/captures/000
- classic: work/coherence-classic-probe-01/000

Do not normalize padding, substitute older config, or infer a fee model. Public
on-chain accounts only; exact requests/receipts/raw hashes preserved, no endpoint key.
