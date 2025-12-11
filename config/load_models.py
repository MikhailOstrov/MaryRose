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
import onnx_asr
import onnxruntime as ort
from dotenv import load_dotenv


load_dotenv() 

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
        available_providers = ort.get_available_providers()
        print(f"🔍 ONNX Runtime Available Providers: {available_providers}")
        
        if 'CUDAExecutionProvider' not in available_providers:
            print("⚠️ WARNING: CUDAExecutionProvider не найден! Инференс будет идти на CPU.")

        local_model_dir = "/app/onnx"
        # Пробуем сначала TensorRT, потом CUDA
        providers = ['TensorrtExecutionProvider', 'CUDAExecutionProvider'] 
        asr_model = onnx_asr.load_model("gigaam-v2-ctc", local_model_dir, providers=providers)
        
        # Проверяем, на каком устройстве реально загрузилась модель
        active_providers = None
        device_info = "❓ Не удалось определить"
        
        try:
            # Пытаемся получить доступ к сессии ONNX Runtime через различные возможные атрибуты
            session = None
            if hasattr(asr_model, 'session'):
                session = asr_model.session
            elif hasattr(asr_model, 'model') and hasattr(asr_model.model, 'session'):
                session = asr_model.model.session
            elif hasattr(asr_model, '_session'):
                session = asr_model._session
            elif hasattr(asr_model, 'onnx_session'):
                session = asr_model.onnx_session
            
            if session is not None:
                active_providers = session.get_providers()
                if 'CUDAExecutionProvider' in active_providers:
                    device_info = "✅ GPU (CUDAExecutionProvider)"
                elif 'CPUExecutionProvider' in active_providers:
                    device_info = "⚠️ CPU (CPUExecutionProvider)"
                else:
                    device_info = f"❓ {active_providers}"
            else:
                # Если не нашли сессию напрямую, пробуем через рефлексию
                print("⚠️ Не удалось найти сессию ONNX Runtime в объекте модели для проверки устройства")
        except Exception as e:
            print(f"⚠️ Ошибка при проверке устройства модели: {e}")
        
        print(f"📊 ASR модель загружена на: {device_info}")
        if active_providers:
            print(f"   Активные провайдеры: {active_providers}")
        
    except Exception as e:
        print(f"Произошла ошибка с загрузкой модели. {e}")
        asr_model = None
    return asr_model

def load_te_model():
    model, example_texts, languages, punct, apply_te = torch.hub.load(repo_or_dir='snakers4/silero-models', model='silero_te')
    return apply_te



# Загрузка моделей перенесена в соответствующие сервисы
# asr_model и te_model больше не загружаются глобально при импорте

__all__ = ['load_asr_model', 'create_new_vad_model', 'load_te_model']
# Экспортируем загруженные модели
