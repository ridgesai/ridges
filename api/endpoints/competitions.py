from fastapi import APIRouter, HTTPException

from models.competition import PublicCompetition
from queries.competition import get_public_competition, get_public_competitions

router = APIRouter(tags=["competitions"])


@router.get("")
async def competition_catalog() -> list[PublicCompetition]:
    """Return every competition that has opened, newest first."""
    return await get_public_competitions()


@router.get("/{set_id}")
async def competition_detail(set_id: int) -> PublicCompetition:
    """Return one opened competition without exposing its stored policy."""
    competition = await get_public_competition(set_id)
    if competition is None:
        raise HTTPException(status_code=404, detail=f"Competition {set_id} not found")
    return competition
