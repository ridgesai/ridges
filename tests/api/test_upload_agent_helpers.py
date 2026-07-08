from unittest.mock import AsyncMock

import pytest
from bittensor_wallet.keypair import Keypair
from fastapi import HTTPException

from api.src.utils import upload_agent_helpers
from api.src.utils.upload_agent_helpers import check_signature


def test_check_signature_accepts_matching_hotkey() -> None:
    keypair = Keypair.create_from_uri("//Alice")
    file_info = f"{keypair.ss58_address}:content-hash:0"
    signature = keypair.sign(file_info).hex()

    check_signature(keypair.public_key.hex(), file_info, signature, keypair.ss58_address)


def test_check_signature_rejects_invalid_signature() -> None:
    signer = Keypair.create_from_uri("//Alice")
    other_keypair = Keypair.create_from_uri("//Bob")
    file_info = f"{signer.ss58_address}:content-hash:0"
    signature = other_keypair.sign(file_info).hex()

    with pytest.raises(HTTPException) as exc_info:
        check_signature(signer.public_key.hex(), file_info, signature, signer.ss58_address)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid signature"


@pytest.mark.anyio
async def test_get_alpha_price_composes_chain_and_tao(monkeypatch) -> None:
    # alpha price in TAO from chain, TAO price in USD from CoinGecko -> alpha price in USD
    monkeypatch.setattr(
        upload_agent_helpers.subtensor_client,
        "get_alpha_price_tao",
        AsyncMock(return_value=0.002),
    )
    monkeypatch.setattr(upload_agent_helpers, "get_tao_price", AsyncMock(return_value=400.0))

    price = await upload_agent_helpers.get_alpha_price()

    assert price == pytest.approx(0.8)  # 0.002 TAO/alpha * 400 USD/TAO
