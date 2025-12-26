import logging
import os
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import FileResponse


from config.logging import setup_logging
from server.TG_Bot.tg_bot_handlers import router as tg_bot_router
from server.Google_Meet.meet_bot_handlers import router as bot_control_router
from server.dependencies import verify_log_access_key
from utils.gpu_monitor import get_gpu_utilization

setup_logging()
# Логгер теперь настраивается uvicorn через --log-config.
# Просто получаем его здесь для использования.
logger = logging.getLogger(__name__)


app = FastAPI(
    title="MaryRose API",
    description="API для управления ботом MaryRose и получения результатов встреч.",
    version="1.0.0"
)

app.include_router(bot_control_router)


app.include_router(tg_bot_router)

@app.get("/logs/app.log", dependencies=[Depends(verify_log_access_key)], tags=["System"])
async def get_app_log():
    """
    Возвращает лог-файл app.log.
    Доступ защищен специальным ключом в заголовке X-Log-Access-Key.
    """
    log_file_path = "logs/app.log"
    if not os.path.exists(log_file_path):
        logger.warning(f"Log file not found at path: {log_file_path}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Log file not found.")
    
    return FileResponse(
        path=log_file_path,
        media_type='text/plain',
        filename='app.log'
    )

@app.get("/health-extended")
async def health_check_extended():
    """
    Возвращает статус сервера, загруженных моделей и текущую загрузку GPU.
    """
    logger.info("Health check extended endpoint was called.")
    gpu_status = get_gpu_utilization()
    
    return {
        "status": "ok", 
        "gpu_metrics": gpu_status if gpu_status else "Not available"
    }

# --- Команда для запуска сервера из терминала ---
# uvicorn server:app --host 0.0.0.0 --port 8001

