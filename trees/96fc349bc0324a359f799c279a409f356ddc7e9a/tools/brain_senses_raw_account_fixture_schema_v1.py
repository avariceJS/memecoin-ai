"""Closed schema constants for synthetic raw-account replay fixtures.

The pinned public-source hashes are provenance locators inherited from the
repository's earlier source survey. This fixture tool does not reopen those
source files or treat the pins as runtime-law qualification.
"""

from __future__ import annotations

RAW_ACCOUNT_FIXTURE_SCHEMA = "brain-senses-raw-account-fixture-v1"
RAW_ACCOUNT_FIXTURE_VERSION = 1

TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"
SYSVAR_OWNER_ID = "Sysvar1111111111111111111111111111111111111"
CLOCK_SYSVAR_ID = "SysvarC1ock11111111111111111111111111111111"
RENT_SYSVAR_ID = "SysvarRent111111111111111111111111111111111"
WSOL_MINT_ID = "So11111111111111111111111111111111111111112"

# These exact refs identify a public upstream revision. The fixtures in this
# schema are synthetic; these hashes are not independently revalidated here.
UPSTREAM_SOURCE_PINS = {
    "repository": "solana-program/token-2022",
    "commit": "b4724e6e7fecc8fc36295f861a769401bf92b270",
    "files": {
        "interface/src/instruction.rs": {
            "url": "https://raw.githubusercontent.com/solana-program/token-2022/"
            "b4724e6e7fecc8fc36295f861a769401bf92b270/interface/src/instruction.rs",
            "sha256": "672b3890cb58e5700a67f202491069ed44dffcbfa0e7d0b83a0cfbbb05b3a287",
        },
        "program/src/processor.rs": {
            "url": "https://raw.githubusercontent.com/solana-program/token-2022/"
            "b4724e6e7fecc8fc36295f861a769401bf92b270/program/src/processor.rs",
            "sha256": "57e451a47aba32e0eac5ab079696b5ff6ac96532b19de5cec4d6bc610fe4ef04",
        },
    },
    "verification": "inherited_pin_metadata_only",
    "fixture_vectors": "synthetic_only",
}

ACCOUNT_ROLES = frozenset({"mint", "token_account", "clock", "rent"})
VECTOR_DOMAINS = frozenset(
    {
        "token2022_fee_config",
        "transfer_hook_control",
        "transfer_hook_unsupported",
        "wsol_native_lifecycle",
        "cpi_attempt_only",
    }
)
STEP_KINDS = frozenset(
    {
        "system_create_account",
        "system_transfer",
        "token_initialize_account3",
        "token_sync_native",
        "token_close_account",
        "token_transfer_checked",
        "hook_invocation",
        "cpi_attempt",
    }
)

FALSE_QUALIFICATION_FLAGS = {
    "runtime_law_qualified": False,
    "economic_admission": False,
}
