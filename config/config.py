from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
AUDIO_FILES_DIR = BASE_DIR / "audio_files"
USER_DATA_DIR = BASE_DIR / "user_data"
UPLOADS_DIR = AUDIO_FILES_DIR / "uploads"
STREAMS_DIR = AUDIO_FILES_DIR / "streams"
MEETINGS_DIR = AUDIO_FILES_DIR / "meetings" # Для записей встреч
TTS_OUTPUT_DIR = BASE_DIR / "static" / "tts_output"
CHROME_PROFILE_DIR = BASE_DIR / "chrome_profile" # Для хранения сессии  Google
SUMMARY_OUTPUT_DIR = BASE_DIR / "summary"

ASR_MODEL_NAME = "deepdml/faster-whisper-large-v3-turbo-ct2"
SPEAKER_MODEL_NAME = "titanet_large"
TTS_MODEL_ID = 'v4_ru'
TTS_SPEAKER = 'xenia'
TTS_SAMPLE_RATE = 48000
LLM_NAME = "meta-llama/Meta-Llama-3.1-8B-Instruct"
DIAR_CONFIG_URL = 'https://raw.githubusercontent.com/NVIDIA/NeMo/main/examples/speaker_tasks/diarization/conf/inference/diar_infer_telephonic.yaml'
DIAR_SPEAKER_MODEL = 'ecapa_tdnn'

STREAM_SAMPLE_RATE = 16000
STREAM_TRIGGER_WORD = "мэри"
VERIFICATION_THRESHOLD = 0.7 

MEET_INPUT_DEVICE_NAME = "pulse"
MEET_GUEST_NAME = "Mary"
MEET_AUDIO_CHUNKS_DIR = AUDIO_FILES_DIR / "meet_chunks"
MEET_VAD_AGGRESSIVENESS = 2  # Агрессивность VAD (0-3, где 3 - самая агрессивная)
MEET_FRAME_DURATION_MS = 30 
MEET_PAUSE_THRESHOLD_S = 2.0  # Пауза в секундах перед завершением записи
SILENCE_THRESHOLD_FRAMES = 16 

def ensure_dirs_exist():
    """Создает все необходимые директории."""
    for path in [UPLOADS_DIR, STREAMS_DIR, TTS_OUTPUT_DIR, USER_DATA_DIR, MEETINGS_DIR, CHROME_PROFILE_DIR, MEET_AUDIO_CHUNKS_DIR]:
        path.mkdir(parents=True, exist_ok=True)
