import os
from pathlib import Path
from openai import OpenAI
from huggingface_hub import login, snapshot_download
import torch
import logging
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
AUDIO_FILES_DIR = BASE_DIR / "audio_files"
USER_DATA_DIR = BASE_DIR / "user_data"
MEETINGS_DIR = AUDIO_FILES_DIR / "meetings" # Для записей встреч
CHROME_PROFILE_DIR = BASE_DIR / "chrome_profile" # Для хранения сессии  Google
SUMMARY_OUTPUT_DIR = BASE_DIR / "summary"
MEET_INPUT_DEVICE_NAME = "pulse"
MEET_GUEST_NAME = "Mary" # Имя ассистента
MEET_AUDIO_CHUNKS_DIR = AUDIO_FILES_DIR / "meet_chunks" 

# --- API ключи и URLs ---
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY")
API_KEY_NAME = os.getenv("API_KEY_NAME")
BACKEND_URL = os.getenv("BACKEND_URL", "https://maryrose.by") # URL бэкенда с БД

logger = logging.getLogger(__name__)

def ensure_dirs_exist():
    """Создает все необходимые директории."""
    for path in [USER_DATA_DIR, MEETINGS_DIR, CHROME_PROFILE_DIR, MEET_AUDIO_CHUNKS_DIR]:
        path.mkdir(parents=True, exist_ok=True)

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
hf_token = os.getenv("HUGGING_FACE_HUB_TOKEN")

# Клиент от OpenAI моделей
CLIENT = OpenAI(
    api_key=os.getenv("PROXY_API"),
    base_url=os.getenv("BASE_OPENAI_URL"),
)

if hf_token:
    login(token=hf_token)
    print("Успешный вход в Hugging Face.")
else:
    print(f"Токен Hugging Face не найден в переменных окружения. {hf_token}")

ASR_MODEL_NAME = "deepdml/faster-whisper-large-v3-turbo-ct2" # Модель Whisper 

STREAM_SAMPLE_RATE = 16000 # Частота для аудиочанков
MEET_FRAME_DURATION_MS = 30 # Размер чанка
MEET_PAUSE_THRESHOLD_S = 1  # Пауза в секундах перед завершением записи
SILENCE_THRESHOLD_FRAMES = 16 # Для определения пауз в речи

STREAM_TRIGGER_WORD = "мэри" # Триггер для работы Мэри

# Триггеры для завершения работы бота
STREAM_STOP_WORD_1 = "стоп"
STREAM_STOP_WORD_2 = "закончи встречу"
STREAM_STOP_WORD_3 = "заверши встречу"

ASSISTANT_PROMPT = """
Ты — умный русскоязычный помощник по имени Мэри. Отвечай только на русском языке, кратко и по существу. Дай четкий и полезный ответ.
Все цифры нужно писать словами, например: два, двадцать три и т.п.
"""

TITLE_PROMPT = """ Ты — русскоязычный ИИ-ассистент. Твоя задача — создать короткий и ёмкий заголовок для встречи на основе её содержания.

ВАЖНЫЕ ПРАВИЛА:
1. ЯЗЫК ОТВЕТА: Отвечай ТОЛЬКО на РУССКОМ языке.
2. ДЛИНА: Заголовок должен быть коротким (максимум 60 символов).
3. СТИЛЬ: Используй формат "Обсуждение темы" или "Планирование проекта".
4. СУТЬ: Отрази главную тему встречи одной фразой.

### Примеры хороших заголовков:
- "Обсуждение запуска новой функции"
- "Планирование маркетинговой кампании"
- "Анализ результатов квартала"
- "Техническое совещание по проекту"
"""

SUMMARY_PROMPT = '''
Я провёл встречу в Google Meet, и вот стенограмма. 
Пожалуйста, проанализируй её и предоставь структурированное резюме. 

Во время анализа исправляй грамматические и стилистические ошибки, чтобы итоговый текст был грамотным.

## ВАЖНЫЕ ПРАВИЛА:
1. ЯЗЫК ОТВЕТА: всегда и без исключений отвечай ТОЛЬКО на РУССКОМ языке.
2. ТОЧНОСТЬ: основываться только на информации из предоставленного диалога, ничего не выдумывать.

Распредели разговор на темы и подтемы, постарайся придать ему хорошую и понятную структуру, как это сделали бы профессионалы.
Для каждой темы должны какие-либо ключевые моменты, чтобы повысить понимание того, что в этой теме важного.
Ключевые моменты - писок основных тезисов и идей, которые обсуждались на встрече, кратко и по делу.
Старайся расположить их в порядке приоритета, думаю, из разговора это понятно (какие-либо технические детали
важнее организационных моментов))

Добавь ещё такую информацию:
### **Задачи для участников**
(Выведи в формате таблицы с колонками: 
- Участник 
- Задача 
- Срок (если указан))
Очень важно, чтобы задачи дл участников были вно указаны в разговоре (пример: Михаилу до завтра нужно разработать прототип приложения), иначе не записывай ничего в задачи.
Оформление таблицы — в Markdown.
'''
