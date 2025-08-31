from pydantic import BaseModel

# Модели данных для запросов
class StartRequest(BaseModel):
    meeting_id: str
    meet_url: str

class StopRequest(BaseModel):
    meeting_id: str

class WebsiteSessionStartRequest(BaseModel):
    meeting_id: int
