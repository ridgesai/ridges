"""One canonical spelling for a burn receipt (block hash + extrinsic index)."""

import re

_BLOCK_HASH = re.compile(r"0x[0-9a-fA-F]{64}")
_EXTRINSIC_INDEX = re.compile(r"[0-9]+")


def canonical_receipt(block_hash: object, extrinsic_index: object) -> tuple[str, str]:
    if not isinstance(block_hash, str) or not _BLOCK_HASH.fullmatch(block_hash):
        raise ValueError("Payment block hash must be 0x followed by 64 hex characters")

    if isinstance(extrinsic_index, int) and not isinstance(extrinsic_index, bool):
        extrinsic_index = str(extrinsic_index)

    if not isinstance(extrinsic_index, str) or not _EXTRINSIC_INDEX.fullmatch(extrinsic_index):
        raise ValueError("Payment extrinsic index must be a non-negative integer")
    return block_hash.lower(), str(int(extrinsic_index))
