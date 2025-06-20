from fastapi import Request, Header, Depends
# from fiber import constants as cst

async def verify_request(
    request: Request,
    # validator_hotkey: str = Header(..., alias=cst.VALIDATOR_HOTKEY),
    # signature: str = Header(..., alias=cst.SIGNATURE),
    # miner_hotkey: str = Header(..., alias=cst.MINER_HOTKEY),
    # nonce: str = Header(..., alias=cst.NONCE),
):
    # To do: make validator push the logs with a fiber method if possible to verify their identity
    return True
