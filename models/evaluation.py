from uuid import UUID
from pydantic import BaseModel



class Evaluation(BaseModel):
    evaluation_id: UUID
    agent_id: UUID
    validator_hotkey: str
    set_id: int