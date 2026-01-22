import os
from pathlib import Path
import torch

# Настройка путей для RunPod (модели сохраняются в персистентный /workspace)
os.environ['HOME'] = '/app'
os.environ['TORCH_HOME'] = '/workspace/.cache/torch'
os.environ['HF_HOME'] = '/workspace/.cache/huggingface'
os.environ['LOGS_DIR'] = '/workspace/logs'

os.environ['OPENBLAS_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'
os.environ['OMP_NUM_THREADS'] = '1'

# Создаем необходимые директории в /workspace
workspace_dirs = [
    '/workspace/.cache/torch',
    '/workspace/.cache/huggingface',
    '/workspace/models',
    '/workspace/logs'
]
for dir_path in workspace_dirs:
    Path(dir_path).mkdir(parents=True, exist_ok=True)
    print(f"Создана директория: {dir_path}")

import torch
from faster_whisper import WhisperModel
from dotenv import load_dotenv

load_dotenv() 

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

# Создает и возвращает НОВЫЙ, ИЗОЛИРОВАННЫЙ экземпляр VAD-модели Silero. Использует кэш, чтобы не скачивать модель каждый раз..
def create_new_vad_model():
    print("Создание нового экземпляра VAD-модели из кэша...")
    model, _ = torch.hub.load(repo_or_dir='snakers4/silero-vad',
                              model='silero_vad',
                              force_reload=False)
    print("✅ Новый экземпляр VAD создан.")
    return model

# Проверка и загрузка ASR модели
def load_asr_model():
    try:
        local_model_dir = "/app/whisper"
        asr_model = WhisperModel(local_model_dir)
        asr_model.to(device)
        print(device)
    except Exception as e:
        print(f"Произошла ошибка с загрузкой модели. {e}")
        asr_model = None
    return asr_model

__all__ = ['load_asr_model', 'create_new_vad_model']
