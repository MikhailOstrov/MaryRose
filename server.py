from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
import logging

# Предполагается, что ваш основной класс для работы с ботом находится здесь
# Если имя файла или класса другое, его нужно будет поменять.
from api.meet_bot import MeetingBot 

# Настройка логирования, чтобы видеть, что происходит
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Backend Server")

class MeetingRequest(BaseModel):
    meeting_id: str
    meet_url: str

def run_bot_in_background(meeting_id: str, meet_url: str):
    """
    Эта функция будет выполняться в фоновом режиме.
    Она инициализирует и запускает бота для одного митинга.
    """
    logger.info(f"Background task started for meeting_id: {meeting_id}")
    try:
        # 1. Создаем экземпляр вашего бота
        # УБЕДИТЕСЬ, ЧТО ИМЯ КЛАССА 'MeetingBot' И МЕТОДА 'join_and_process' ВЕРНЫЕ
        bot = MeetingBot(meet_url=meet_url, meeting_id=meeting_id)

        # 2. Запускаем основную долгую задачу
        bot.join_and_process() 

        logger.info(f"Background task finished for meeting_id: {meeting_id}")

    except Exception as e:
        logger.error(f"Error in background task for meeting_id {meeting_id}: {e}", exc_info=True)


@app.post("/internal/start-processing")
async def start_processing(request: MeetingRequest, background_tasks: BackgroundTasks):
    """
    Этот эндпоинт немедленно принимает запрос, добавляет долгую задачу 
    по запуску бота в фон и сразу же возвращает ответ.
    """
    logger.info(f"Received request to start processing for meeting_id: {request.meeting_id}")
    
    # Добавляем нашу долгую функцию в фоновые задачи
    background_tasks.add_task(run_bot_in_background, request.meeting_id, request.meet_url)
    
    # Сразу возвращаем ответ, не дожидаясь завершения бота
    return {"status": "accepted", "message": f"Bot for meeting {request.meeting_id} has been scheduled."}


@app.get("/health")
async def health_check():
    """Простой эндпоинт для проверки, что сервер жив."""
    return {"status": "ok"}

# Если вы хотите запускать этот файл напрямую для теста:
# uvicorn server:app --host 0.0.0.0 --port 8001
