from fastapi import HTTPException

from models.competition import PublicCompetition
from queries.competition import get_public_competition, resolve_compatibility_competition_set_id


async def resolve_optional_public_competition(set_id: int | None = None) -> PublicCompetition:
    """Resolve an explicit public competition, or the compatibility default for None/-1."""
    if set_id is None or set_id == -1:
        set_id = await resolve_compatibility_competition_set_id()

    competition = None if set_id is None else await get_public_competition(set_id)
    if competition is None:
        raise HTTPException(status_code=404, detail="No live competition found")
    return competition
