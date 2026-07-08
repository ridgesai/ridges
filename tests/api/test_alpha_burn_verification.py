import pytest
from fastapi import HTTPException

from api.src.endpoints import upload as upload_module
from api.src.endpoints.upload import find_alpha_burned_event, verify_burn_extrinsic

COLDKEY = "5C8769orColdkey"


def _burn_event(idx, coldkey=COLDKEY, netuid=62, amount=120_344_620_287_164):
    return {
        "extrinsic_idx": idx,
        "event": {
            "module_id": "SubtensorModule",
            "event_id": "AlphaBurned",
            "attributes": {
                "Coldkey": coldkey,
                "Hotkey": "5FHhot",
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


def test_find_alpha_burned_event_returns_attributes():
    events = [_burn_event(2)]
    attrs = find_alpha_burned_event(events, 2)
    assert upload_module._event_attr(attrs, "Netuid", "netuid") == 62


def test_find_alpha_burned_event_missing_raises_402():
    events = [_burn_event(2)]
    with pytest.raises(HTTPException) as exc:
        find_alpha_burned_event(events, 5)
    assert exc.value.status_code == 402


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
