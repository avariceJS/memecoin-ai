#!/usr/bin/env node
"use strict";

// Source-pinned offline derivation helper. This intentionally performs no RPC/network access.
const path = require("node:path");
if (!process.argv[2]) throw new Error("sdk_root_required");
const sdkRoot = path.resolve(process.argv[2]);

const { PublicKey } = require("@solana/web3.js");
const { getAssociatedTokenAddressSync } = require("@solana/spl-token");
const { poolV2Pda, userVolumeAccumulatorPda } = require(path.join(sdkRoot, "dist/sdk/pda.js"));

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", () => {
  const rows = JSON.parse(input);
  if (!Array.isArray(rows)) throw new Error("input_must_be_array");
  const output = rows.map((row) => {
    const baseMint = new PublicKey(row.base_mint);
    const user = new PublicKey(row.user);
    const quoteMint = new PublicKey(row.quote_mint);
    const quoteTokenProgram = new PublicKey(row.quote_token_program);
    const accumulator = userVolumeAccumulatorPda(user);
    return {
      slot: row.slot,
      transaction_index: row.transaction_index,
      path: row.path,
      pool_v2: poolV2Pda(baseMint).toBase58(),
      cashback_quote_ata: getAssociatedTokenAddressSync(
        quoteMint, accumulator, true, quoteTokenProgram
      ).toBase58(),
    };
  });
  process.stdout.write(JSON.stringify(output));
});
