import os
from pathlib import Path
import torch
from omegaconf import OmegaConf

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
import onnx_asr
from dotenv import load_dotenv


from config.config import hf_token

load_dotenv() 

# Создает и возвращает НОВЫЙ, ИЗОЛИРОВАННЫЙ экземпляр VAD-модели Silero. Использует кэш, чтобы не скачивать модель каждый раз.
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
        local_model_dir = "/app/onnx"
        providers = ['CUDAExecutionProvider'] 
        asr_model = onnx_asr.load_model("gigaam-v2-ctc", local_model_dir, providers=providers)
    except Exception as e:
        print(f"Произошла ошибка с загрузкой модели. {e}")
    return asr_model

def load_te_model():
    model, example_texts, languages, punct, apply_te = torch.hub.load(repo_or_dir='snakers4/silero-models', model='silero_te')
    return apply_te


# Загрузка моделей при импорте модуля
print("=== Начинаем загрузку моделей в /workspace ===")
asr_model = load_asr_model()
te_model = load_te_model()
print("=== Все модели успешно загружены ===")

__all__ = ['asr_model', 'create_new_vad_model', 'te_model']
# Экспортируем загруженные модели