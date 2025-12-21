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
import shutil
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

def optimize_model_if_needed(model_path):
    """Оптимизирует ONNX модель для устранения проблем с Memcpy на GPU"""
    marker = model_path + ".optimized"
    if os.path.exists(marker):
        return

    print(f"🔄 Оптимизация модели {model_path} для устранения Memcpy узлов...")
    try:
        opt_path = model_path + ".temp"
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.optimized_model_filepath = opt_path
        
        # Запускаем сессию для триггера оптимизации (на CUDA, чтобы знать возможности GPU)
        # Если CUDA недоступна, сработает фоллбек, но оптимизация все равно пройдет
        _ = ort.InferenceSession(model_path, so, providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
        
        # Заменяем оригинал
        if os.path.exists(opt_path):
            shutil.move(opt_path, model_path)
            # Создаем маркер
            with open(marker, 'w') as f: f.write("done")
            print("✅ Модель успешно оптимизирована и заменена.")
        
    except Exception as e:
        print(f"⚠️ Ошибка оптимизации (игнорируем, попробуем запустить так): {e}")

# Проверка и загрузка ASR модели
def load_asr_model():
    try:
        available_providers = ort.get_available_providers()
        print(f"🔍 ONNX Runtime Available Providers: {available_providers}")
        
        local_model_dir = "/app/onnx"
        providers = ['CUDAExecutionProvider'] 
        asr_model = onnx_asr.load_model("gigaam-v3-e2e-ctc", local_model_dir, providers=providers)
        
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

__all__ = ['load_asr_model', 'create_new_vad_model']
