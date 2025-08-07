from typing import List
from api.src.backend.entities import InferenceSummary

async def get_inferences(since_hours: int = 10) -> List[InferenceSummary]: ...