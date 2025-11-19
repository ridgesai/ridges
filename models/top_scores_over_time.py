from datetime import datetime
from pydantic import BaseModel

class TopScoreOverTime(BaseModel):
    hour: datetime
    top_score: float
