from unittest.mock import AsyncMock

import pytest
from bittensor_wallet.keypair import Keypair
from fastapi import HTTPException

from api.src.utils import upload_agent_helpers
from api.src.utils.upload_agent_helpers import check_signature, find_alpha_burned_event, verify_burn_extrinsic

COLDKEY = "5C8769orColdkey"
HOTKEY = "5FHhot"


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


# ── AlphaBurned event parsing + burn extrinsic verification ────────────────────


def _burn_event_tuple(idx, coldkey=COLDKEY, netuid=62, amount=120_344_620_287_164):
    """A burn event with positional-tuple attributes — the confirmed live shape from AsyncSubtensor."""
    return {
        "extrinsic_idx": idx,
        "event": {
            "module_id": "SubtensorModule",
            "event_id": "AlphaBurned",
            "attributes": (coldkey, HOTKEY, amount, netuid),
        },
    }


def _burn_event_dict(idx, coldkey=COLDKEY, netuid=62, amount=120_344_620_287_164):
    """A burn event with dict-shaped attributes (fallback shape, not the confirmed live one)."""
    return {
        "extrinsic_idx": idx,
        "event": {
            "module_id": "SubtensorModule",
            "event_id": "AlphaBurned",
            "attributes": {
                "Coldkey": coldkey,
                "Hotkey": HOTKEY,
                "Actual Alpha Decrease": amount,
                "Netuid": netuid,
            },
        },
    }


def _extrinsic(coldkey=COLDKEY, call_function="burn_alpha", call_module="SubtensorModule"):
    class Ext:
        value_serialized = {
            "address": coldkey,
            "call": {"call_module": call_module, "call_function": call_function, "call_args": []},
        }

    return Ext()


def test_find_alpha_burned_event_parses_tuple_shape():
    """Confirmed live shape: a positional tuple (coldkey, hotkey, actual_alpha_decrease, netuid)."""
    events = [_burn_event_tuple(2, amount=115_259_028_589, netuid=62)]
    burn_event = find_alpha_burned_event(events, 2, netuid=62)
    assert burn_event.coldkey == COLDKEY
    assert burn_event.hotkey == HOTKEY
    assert burn_event.alpha_decrease == 115_259_028_589
    assert burn_event.netuid == 62


def test_find_alpha_burned_event_parses_dict_shape():
    """Dict-shaped attributes are supported as a fallback."""
    events = [_burn_event_dict(2, amount=120_344_620_287_164, netuid=62)]
    burn_event = find_alpha_burned_event(events, 2, netuid=62)
    assert burn_event.coldkey == COLDKEY
    assert burn_event.hotkey == HOTKEY
    assert burn_event.alpha_decrease == 120_344_620_287_164
    assert burn_event.netuid == 62


def test_find_alpha_burned_event_missing_raises_402():
    events = [_burn_event_tuple(2)]
    with pytest.raises(HTTPException) as exc:
        find_alpha_burned_event(events, 5, netuid=62)
    assert exc.value.status_code == 402


def test_find_alpha_burned_event_skips_wrong_netuid():
    """Events on a different netuid are ignored; only the matching netuid is returned."""
    events = [
        _burn_event_tuple(2, amount=111, netuid=1),
        _burn_event_tuple(2, amount=222, netuid=62),
    ]
    burn_event = find_alpha_burned_event(events, 2, netuid=62)
    assert burn_event.netuid == 62
    assert burn_event.alpha_decrease == 222


def test_verify_burn_extrinsic_accepts_burn_alpha():
    verify_burn_extrinsic(_extrinsic(call_function="burn_alpha"), COLDKEY)


def test_verify_burn_extrinsic_accepts_add_stake_burn():
    verify_burn_extrinsic(_extrinsic(call_function="add_stake_burn"), COLDKEY)


def test_verify_burn_extrinsic_rejects_non_burn_call():
    with pytest.raises(HTTPException) as exc:
        verify_burn_extrinsic(_extrinsic(call_function="transfer_keep_alive", call_module="Balances"), COLDKEY)
    assert exc.value.status_code == 402


def test_verify_burn_extrinsic_rejects_wrong_signer():
    with pytest.raises(HTTPException) as exc:
        verify_burn_extrinsic(_extrinsic(coldkey="5Fother"), COLDKEY)
    assert exc.value.status_code == 402
