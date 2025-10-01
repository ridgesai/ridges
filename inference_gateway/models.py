from uuid import UUID
from typing import List
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict



class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='PROXY_', env_file='.env')
    
    HOST: str
    PORT: int
    CHUTES_BASE_URL: str
    CHUTES_API_KEY: str
    CHUTES_EMBEDDING_URL: str



class Message(BaseModel):
    role: str
    content: str

class InferenceRequest(BaseModel):
    run_id: UUID
    model: str
    temperature: float
    messages: List[Message]



class EmbeddingRequest(BaseModel):
    run_id: UUID
    input: str