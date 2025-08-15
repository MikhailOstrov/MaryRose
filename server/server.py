import logging
from fastapi import FastAPI
import threading

from api import websocket_gateway

from server.TG_Bot.tg_bot_handlers import router as tg_bot_router
from server.Google_Meet.meet_bot_handlers import router as bot_control_router
from server.Google_Meet.meet_bot_manager import launch_worker

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(
    title="MaryRose API",
    description="API для управления ботом MaryRose и получения результатов встреч.",
    version="1.0.0"
)

@app.on_event("startup")
def startup_event():
    """
    Запускает фоновый поток для воркера, который будет обрабатывать очередь.
    """
    worker_thread = threading.Thread(target=launch_worker, daemon=True)
    worker_thread.name = "BotLaunchWorker"
    worker_thread.start()

app.include_router(bot_control_router)

app.include_router(websocket_gateway.router, prefix="/ws")

app.include_router(tg_bot_router)

# --- Команда для запуска сервера из терминала ---
# uvicorn server:app --host 0.0.0.0 --port 8001