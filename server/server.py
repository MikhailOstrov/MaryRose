import logging

from api import websocket_gateway
from dependencies import app

from server.TG_Bot.tg_bot_handlers import router as tg_bot_router
from server.Google_Meet.meet_bot_handlers import router as bot_control_router

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app.include_router(bot_control_router)

app.include_router(websocket_gateway.router, prefix="/ws")

app.include_router(tg_bot_router)

# --- Команда для запуска сервера из терминала ---
# uvicorn server:app --host 0.0.0.0 --port 8001