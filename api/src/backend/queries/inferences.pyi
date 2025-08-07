from typing import List
from datetime import datetime

from api.src.backend.entities import ProviderStatistics

async def get_inference_provider_statistics(start_time: datetime, end_time: datetime) -> List[ProviderStatistics]: ...