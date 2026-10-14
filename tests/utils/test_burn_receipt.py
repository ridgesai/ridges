import pytest

from utils.burn_receipt import canonical_receipt
from utils.upload_ticket import cancel_signing_string, confirm_signing_string

HASH = "0x" + "ab" * 32


def test_canonical_receipt_passes_canonical_values_through():
    assert canonical_receipt(HASH, "14") == (HASH, "14")
    assert canonical_receipt(HASH, 7) == (HASH, "7")


def test_canonical_receipt_lowercases_hash_and_strips_leading_zeros():
    assert canonical_receipt("0x" + "AB" * 32, "0014") == (HASH, "14")
    assert canonical_receipt(HASH, "0") == (HASH, "0")


@pytest.mark.parametrize("index", ["+1", " 1 ", "1_0", "-1", "", "1.0", True, None, -1])
def test_canonical_receipt_rejects_non_canonical_indexes(index):
    with pytest.raises(ValueError):
        canonical_receipt(HASH, index)


@pytest.mark.parametrize("block_hash", ["0xdeadbeef", "ab" * 32, "0X" + "ab" * 32, "0x" + "zz" * 32, None])
def test_canonical_receipt_rejects_bad_hashes(block_hash):
    with pytest.raises(ValueError):
        canonical_receipt(block_hash, "1")


def test_confirm_and_cancel_signing_strings_are_domain_separated():
    assert confirm_signing_string("hk", "q", HASH, "1") == f"ridges-upload-confirm:v1:hk:q:{HASH}:1"
    assert cancel_signing_string("hk", "q") == "ridges-upload-cancel:v1:hk:q"
