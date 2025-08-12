import os
from pathlib import Path

# Настройка путей для RunPod (модели сохраняются в персистентный /workspace)
os.environ['HOME'] = '/app'
os.environ['TORCH_HOME'] = '/workspace/.cache/torch'
os.environ['NEMO_CACHE_DIR'] = '/workspace/.cache/nemo'
os.environ['HF_HOME'] = '/workspace/.cache/huggingface'

# Создаем необходимые директории в /workspace
workspace_dirs = [
    '/workspace/.cache/torch',
    '/workspace/.cache/nemo', 
    '/workspace/.cache/huggingface',
    '/workspace/models'
]
for dir_path in workspace_dirs:
    Path(dir_path).mkdir(parents=True, exist_ok=True)
    print(f"Создана директория: {dir_path}")

from faster_whisper import WhisperModel
from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline
from huggingface_hub import login, snapshot_download
from omegaconf import OmegaConf
import torch
import wget
from config import ASR_MODEL_NAME, TTS_MODEL_ID, DIAR_SPEAKER_MODEL, DIAR_CONFIG_URL, LLM_NAME
from dotenv import load_dotenv

load_dotenv() 

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
hf_token = os.getenv("HUGGING_FACE_HUB_TOKEN")

if hf_token:
    login(token=hf_token)
    print("Успешный вход в Hugging Face.")
else:
    print(f"Токен Hugging Face не найден в переменных окружения. {hf_token}")

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
    # Загружаем локальный путь к модели из кэша HF. Если кэш не найден — упадем, чтобы не ходить в сеть.
    print(f"Проверка локального кэша для ASR модели: {ASR_MODEL_NAME}")
    local_path = snapshot_download(
        repo_id=ASR_MODEL_NAME,
        cache_dir="/workspace/.cache/huggingface",
        local_files_only=True,
        token=hf_token
    )
    print(f"Найден локальный путь ASR модели: {local_path}")
    asr_model = WhisperModel(local_path)
    print("ASR model loaded.")
    return asr_model

# Проверка и загрузка Whisper
def load_silero_vad_model():
    if check_model_exists("silero-vad", "torch_hub"):
        print("Silero VAD модель найдена в /workspace, загружаем...")
    else:
        print("Silero VAD модель не найдена, загружаем в /workspace...")
    
    model, utils = torch.hub.load(repo_or_dir='snakers4/silero-vad',
                                  model='silero_vad',
                                  force_reload=False)
    print("Silero VAD model loaded.")
    (get_speech_timestamps, _, _, VADIterator, _) = utils
    iterator = VADIterator(model,
                           threshold=0.1)
    return model, utils, iterator

# Проверка и загрузка модели TTS
def load_tts_model():
    if check_model_exists("silero-models", "torch_hub"):
        print(f"TTS модель {TTS_MODEL_ID} найдена в /workspace, загружаем...")
    else:
        print(f"TTS модель {TTS_MODEL_ID} не найдена, загружаем в /workspace...")
    
    tts_model, _ = torch.hub.load(repo_or_dir='snakers4/silero-models', model='silero_tts', language='ru', speaker=TTS_MODEL_ID, trust_repo=True)
    tts_model.to(device)
    print("TTS model loaded.")
    return tts_model

# Проверка и загрузка конфига диаризации
def load_diarizer_config():
    config_path = Path("/workspace/models/diar_infer_telephonic.yaml")
    if not config_path.exists():
        print("Diarizer конфигурация не найдена, загружаем...")
        wget.download(DIAR_CONFIG_URL, str(config_path))
    else:
        print("Diarizer конфигурация найдена в /workspace")
    
    config = OmegaConf.load(config_path)
    config.diarizer.speaker_embeddings.model_path = DIAR_SPEAKER_MODEL
    return config

# Проверка и загрузка Llama
def load_llm():
    if check_model_exists(LLM_NAME, "huggingface"):
        print(f"LLM модель {LLM_NAME} найдена в локальном кэше, загружаем из него...")
    else:
        print(f"LLM модель {LLM_NAME} не найдена в локальном кэше. Убедитесь, что выполнен pre-cache.")

    tokenizer = AutoTokenizer.from_pretrained(
        LLM_NAME,
        local_files_only=True,
        token=hf_token
    )
    model = AutoModelForCausalLM.from_pretrained(
        LLM_NAME,
        local_files_only=True,
        torch_dtype=torch.bfloat16,
        device_map="cuda",
        token=hf_token
    )
    text_gen = pipeline(
        "text-generation",
        model=model,
        tokenizer=tokenizer,
        device_map="cuda",
    )
    return text_gen

# Загрузка моделей при импорте модуля
print("=== Начинаем загрузку моделей в /workspace ===")
llm_model = load_llm()
asr_model = load_asr_model()
(vad_model, vad_utils, vad_iterator) = load_silero_vad_model()
tts_model = load_tts_model()
diarizer_config = load_diarizer_config()
print("=== Все модели успешно загружены ===")

# Экспортируем загруженные модели
__all__ = ['llm_model', 'asr_model', 'vad_model', 'vad_utils', 'vad_iterator', 'tts_model', 'diarizer_config']