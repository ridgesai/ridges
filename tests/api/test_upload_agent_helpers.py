import pytest
from bittensor_wallet.keypair import Keypair
from fastapi import HTTPException

from api.src.utils.upload_agent_helpers import check_signature


def test_check_signature_accepts_matching_hotkey() -> None:
    keypair = Keypair.create_from_uri("//Alice")
    file_info = f"{keypair.ss58_address}:content-hash:0"
    signature = keypair.sign(file_info).hex()

    check_signature(keypair.public_key.hex(), file_info, signature)


def test_check_signature_rejects_invalid_signature() -> None:
    signer = Keypair.create_from_uri("//Alice")
    verifier = Keypair.create_from_uri("//Bob")
    file_info = f"{signer.ss58_address}:content-hash:0"
    signature = signer.sign(file_info).hex()

    with pytest.raises(HTTPException) as exc_info:
        check_signature(verifier.public_key.hex(), file_info, signature)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid signature"
