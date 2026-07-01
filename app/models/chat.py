from pydantic import BaseModel

class ChatRequest(BaseModel):
    session_id: str
    user_prompt: str

class ChatResponse(BaseModel):
    intent: str
    answer: str
    entities: dict | None = None
    status: str | None = None