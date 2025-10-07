from uuid import UUID
from typing import Optional
from api.src.backend.queries.agents import get_oldest_version_id_awaiting_screening
from api.src.backend.queries.evaluations import create_screener_evaluation_and_runs_for_agent



# Gets the next evaluation ID for a given screener class (either 1 or 2)
async def _get_next_evaluation_id_for_screener(screener_hotkey: str) -> Optional[UUID]:
    # Extract the screener class from the screener hotkey
    # TODO: Assumes screener_hotkey is valid (i.e., "screener-1-X" or "screener-2-X"). Maybe sanity check this?
    screener_class = int(screener_hotkey.split("-")[1])

    # We only support screener-1 and screener-2, for now
    if (screener_class not in [1, 2]):
        raise ValueError(f"Invalid screener number: {screener_class}")
    
    # First, find the oldest miner that is awaiting_screening_X (if any)
    version_id = await get_oldest_version_id_awaiting_screening(screener_class)
    if not version_id:
        return None

    # Create a new evaluation for this miner, and assign it to the screener
    return await create_screener_evaluation_and_runs_for_agent(version_id, screener_hotkey)



# Gets the next evaluation ID for a given validator.
async def _get_next_evaluation_id_for_validator(validator_hotkey: str) -> Optional[UUID]:
    # TODO
    pass



# Gets the next evaluation ID for a given validator hotkey (just dispatches to the appropriate function)
async def get_next_evaluation_id_for_validator_hotkey(validator_hotkey: str) -> Optional[UUID]:
    if validator_hotkey.startswith("screener-1-"):
        return _get_next_evaluation_id_for_screener(validator_hotkey)
    elif validator_hotkey.startswith("screener-2-"):
        return _get_next_evaluation_id_for_screener(validator_hotkey)
    else:
        return _get_next_evaluation_id_for_validator(validator_hotkey)