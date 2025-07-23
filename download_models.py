# download_models.py
import os
import torch

# --- Настройка путей для кэша внутри Docker ---
# Это гарантирует, что модели будут кэшироваться в рабочей директории /app,
# а не в системной /root, что является хорошей практикой.
os.environ['HOME'] = '/app'
os.environ['TORCH_HOME'] = '/app/.cache/torch'
os.environ['NEMO_CACHE_DIR'] = '/app/.cache/nemo'

print("--- Pre-loading all ML models ---")
print(f"PyTorch version: {torch.__version__}")
print(f"CUDA available: {torch.cuda.is_available()}")

# Импортируем хендлеры для загрузки моделей
# Эти импорты должны сработать, так как папка api/ будет скопирована до запуска скрипта
from api import tts_handler, stt_handler, diarization_handler, speaker_handler
from config import ensure_dirs_exist

# Создаем все необходимые директории согласно config.py
ensure_dirs_exist()

# Последовательно запускаем функции загрузки моделей
print("\nLoading TTS model...")
tts_handler.load_tts_model()

print("\nLoading ASR model...")
stt_handler.load_asr_model()

print("\nLoading Diarization model...")
diarization_handler.load_diarizer_model()

print("\nLoading Speaker Recognition model...")
speaker_handler.load_speaker_model()

print("\n--- All models have been pre-loaded successfully ---")
