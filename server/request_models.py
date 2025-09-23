from pydantic import BaseModel

# Модели данных для запросов
class StartRequest(BaseModel):
    meeting_id: str
    meet_url: str
    email: str
    remaining_seconds: int

class StopRequest(BaseModel):
    meeting_id: str

class WebsiteSessionStartRequest(BaseModel):
    meeting_id: int
