from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
AUDIO_FILES_DIR = BASE_DIR / "audio_files"
USER_DATA_DIR = BASE_DIR / "user_data"
UPLOADS_DIR = AUDIO_FILES_DIR / "uploads"
STREAMS_DIR = AUDIO_FILES_DIR / "streams"
MEETINGS_DIR = AUDIO_FILES_DIR / "meetings" # Для записей встреч
TTS_OUTPUT_DIR = BASE_DIR / "static" / "tts_output"
CHROME_PROFILE_DIR = BASE_DIR / "chrome_profile" # Для хранения сессии Google


ASR_MODEL_NAME = "nvidia/stt_ru_fastconformer_hybrid_large_pc"
SPEAKER_MODEL_NAME = "titanet_large"
TTS_MODEL_ID = 'v4_ru'
TTS_SPEAKER = 'xenia'
TTS_SAMPLE_RATE = 48000
DIAR_CONFIG_URL = 'https://raw.githubusercontent.com/NVIDIA/NeMo/main/examples/speaker_tasks/diarization/conf/inference/diar_infer_telephonic.yaml'
DIAR_SPEAKER_MODEL = 'ecapa_tdnn'

STREAM_SAMPLE_RATE = 16000
STREAM_TRIGGER_WORD = "мэри"
VERIFICATION_THRESHOLD = 0.7 

MEET_INPUT_DEVICE_NAME = "pulse"
MEET_GUEST_NAME = "Mary"
MEET_AUDIO_CHUNKS_DIR = AUDIO_FILES_DIR / "meet_chunks"
MEET_VAD_AGGRESSIVENESS = 3 
MEET_FRAME_DURATION_MS = 30 
MEET_PAUSE_THRESHOLD_S = 0.8 

def ensure_dirs_exist():
    """Создает все необходимые директории."""
    for path in [UPLOADS_DIR, STREAMS_DIR, TTS_OUTPUT_DIR, USER_DATA_DIR, MEETINGS_DIR, CHROME_PROFILE_DIR, MEET_AUDIO_CHUNKS_DIR]:
        path.mkdir(parents=True, exist_ok=True)
