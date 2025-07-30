# config.py
from pathlib import Path

# --- Основные пути ---
BASE_DIR = Path(__file__).resolve().parent
AUDIO_FILES_DIR = BASE_DIR / "audio_files"
USER_DATA_DIR = BASE_DIR / "user_data"
UPLOADS_DIR = AUDIO_FILES_DIR / "uploads"
STREAMS_DIR = AUDIO_FILES_DIR / "streams"
MEETINGS_DIR = AUDIO_FILES_DIR / "meetings" # Для записей встреч
TTS_OUTPUT_DIR = BASE_DIR / "static" / "tts_output"
CHROME_PROFILE_DIR = BASE_DIR / "chrome_profile" # Для хранения сессии Google

# --- Параметры моделей ---
ASR_MODEL_NAME = "nvidia/stt_ru_fastconformer_hybrid_large_pc"
SPEAKER_MODEL_NAME = "titanet_large"
TTS_MODEL_ID = 'v4_ru'
TTS_SPEAKER = 'xenia'
TTS_SAMPLE_RATE = 48000
DIAR_CONFIG_URL = 'https://raw.githubusercontent.com/NVIDIA/NeMo/main/examples/speaker_tasks/diarization/conf/inference/diar_infer_telephonic.yaml'
DIAR_SPEAKER_MODEL = 'ecapa_tdnn'

# --- Параметры обработки ---
STREAM_SAMPLE_RATE = 16000
STREAM_TRIGGER_WORD = "мэри"
VERIFICATION_THRESHOLD = 0.7 

# --- Параметры Ollama ---
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "llama3:8b-instruct-q4_K_M"

OLLAMA_ASSISTANT_PROMPT = """
Ты — ассистент по имени Мэри. Тебе дали следующую команду: "{command}".
Выполни эту команду. Отвечай кратко и по делу.
"""
OLLAMA_SUMMARY_PROMPT = """
Ты — высококлассный ассистент для анализа совещаний. Твоя задача — проанализировать следующий диалог и предоставить краткое резюме.
Результат должен быть структурирован строго по следующему формату:

### Ключевые моменты
- [Основная мысль или решение 1]
- [Основная мысль или решение 2]
- [и так далее...]

### Задачи для участников
- **[Имя участника]:** [Описание задачи, которую ему поручили]
- **[Имя участника]:** [Описание другой задачи]

Вот диалог для анализа:
---
{dialogue_text}
---
"""

# --------------------------------------------------------------------------
# Параметры бота для Google Meet
# --------------------------------------------------------------------------
# Имя виртуального аудиоустройства для записи звука из Chrome
# Важно: это имя должно соответствовать тому, как его видит библиотека sounddevice.
# Используем 'pulse', так как sounddevice видит сервер PulseAudio как одно устройство.
MEET_INPUT_DEVICE_NAME = "pulse"
# Имя, под которым бот будет входить на встречу
MEET_GUEST_NAME = "Mary"
MEET_AUDIO_CHUNKS_DIR = AUDIO_FILES_DIR / "meet_chunks" # Куда сохранять чанки
#some change
# --- Параметры VAD (Voice Activity Detection) ---
MEET_VAD_AGGRESSIVENESS = 3         # от 0 (наименее агрессивный) до 3 (наиболее агрессивный). ИСПРАВЛЕНО: как в рабочем join_meet!
MEET_FRAME_DURATION_MS = 30         # длительность фрейма в миллисекундах (10, 20 или 30)
MEET_PAUSE_THRESHOLD_S = 0.8        # сколько секунд тишины считать концом фразы

def ensure_dirs_exist():
    """Создает все необходимые директории."""
    for path in [UPLOADS_DIR, STREAMS_DIR, TTS_OUTPUT_DIR, USER_DATA_DIR, MEETINGS_DIR, CHROME_PROFILE_DIR, MEET_AUDIO_CHUNKS_DIR]:
        path.mkdir(parents=True, exist_ok=True)
