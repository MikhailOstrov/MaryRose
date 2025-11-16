import os
from pathlib import Path

# Настройка путей для RunPod (модели сохраняются в персистентный /workspace)
os.environ['HOME'] = '/app'
os.environ['TORCH_HOME'] = '/workspace/.cache/torch'
os.environ['NEMO_CACHE_DIR'] = '/workspace/.cache/nemo'
os.environ['HF_HOME'] = '/workspace/.cache/huggingface'
os.environ['LOGS_DIR'] = '/workspace/logs'

# Создаем необходимые директории в /workspace
workspace_dirs = [
    '/workspace/.cache/torch',
    '/workspace/.cache/nemo', 
    '/workspace/.cache/huggingface',
    '/workspace/models',
    '/workspace/logs'
]
for dir_path in workspace_dirs:
    Path(dir_path).mkdir(parents=True, exist_ok=True)
    print(f"Создана директория: {dir_path}")

from faster_whisper import WhisperModel
from huggingface_hub import snapshot_download
from dotenv import load_dotenv
import torch

from config.config import ASR_MODEL_NAME, hf_token

load_dotenv() 

# Создает и возвращает НОВЫЙ, ИЗОЛИРОВАННЫЙ экземпляр VAD-модели Silero. Использует кэш, чтобы не скачивать модель каждый раз./
def create_new_vad_model():
    print("Создание нового экземпляра VAD-модели из кэша...")
    model, _ = torch.hub.load(repo_or_dir='snakers4/silero-vad',
                              model='silero_vad',
                              force_reload=False)
    print("✅ Новый экземпляр VAD создан.")
    return model

# Функция проверки, загружены ли модели
def check_model_exists(model_identifier, model_type="whisper"):
    """Проверяет существование модели в /workspace"""
    if model_type == "whisper":
        model_path = Path(f"/workspace/.cache/torch/whisper/{model_identifier}")
        return model_path.exists()
    elif model_type == "huggingface":
        hf_cache = Path(f"/workspace/.cache/huggingface/hub")
        if not hf_cache.exists():
            return False
        for model_dir in hf_cache.iterdir():
            if model_identifier.replace("/", "--") in model_dir.name:
                return True
        return False
    elif model_type == "torch_hub":
        torch_cache = Path(f"/workspace/.cache/torch/hub")
        return torch_cache.exists() and any(torch_cache.iterdir())
    return False

# Проверка и загрузка Whisper
def load_asr_model():
    print(f"Проверка локального кэша для ASR модели: {ASR_MODEL_NAME}")
    try:
        local_path = snapshot_download(
            repo_id=ASR_MODEL_NAME,
            cache_dir="/workspace/.cache/huggingface",
            local_files_only=True,
            token=hf_token
        )
        print(f"Найден локальный путь ASR модели: {local_path}")
    except Exception as e:
        print(f"Локальный кэш ASR не найден, скачиваю из сети: {e}")
        local_path = snapshot_download(
            repo_id=ASR_MODEL_NAME,
            cache_dir="/workspace/.cache/huggingface",
            local_files_only=False,
            token=hf_token
        )
        print(f"ASR модель скачана в: {local_path}")
    asr_model = WhisperModel(local_path, compute_type="float16")
    print("ASR model loaded.")
    return asr_model

# Загрузка моделей при импорте модуля
print("=== Начинаем загрузку моделей в /workspace ===")
asr_model = load_asr_model()
print("=== Все модели успешно загружены ===")

# Экспортируем загруженные модели
__all__ = ['llm_model', 'asr_model', 'create_new_vad_model']