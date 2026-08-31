from fastapi import APIRouter, HTTPException

from models.competition import PublicCompetition
from queries.competition import get_public_competition, get_public_competitions

router = APIRouter(tags=["competitions"])


@router.get("")
async def competition_catalog(accepting: bool | None = None) -> list[PublicCompetition]:
    """Return every competition that has opened, newest first.
    This route must stay uncached.
    """
    competitions = await get_public_competitions()
    if accepting is None:
        return competitions
    return [competition for competition in competitions if competition.accepting == accepting]


@router.get("/{set_id}")
async def competition_detail(set_id: int) -> PublicCompetition:
    """Return one opened competition without exposing its stored policy."""
    competition = await get_public_competition(set_id)
    if competition is None:
        raise HTTPException(status_code=404, detail=f"Competition {set_id} not found")
    return competition
