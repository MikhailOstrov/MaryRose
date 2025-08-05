import os

os.environ['HOME'] = '/app'
os.environ['TORCH_HOME'] = '/app/.cache/torch'
os.environ['NEMO_CACHE_DIR'] = '/app/.cache/nemo'

from faster_whisper import WhisperModel
import transformers
from huggingface_hub import login
from omegaconf import OmegaConf
import torch
import wget
from pathlib import Path
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

def load_asr_model():
    asr_model = WhisperModel(ASR_MODEL_NAME)
    print("ASR model loaded.")
    return asr_model

def load_silero_vad_model():
    model, utils = torch.hub.load(repo_or_dir='snakers4/silero-vad',
                                  model='silero_vad',
                                  force_reload=False)
    print("Silero VAD model loaded.")
    (get_speech_timestamps, _, _, VADIterator, _) = vad_utils
    iterator = VADIterator(vad_model,
                           threshold=0.1)
    return model, utils, iterator

def load_tts_model():
    tts_model, _ = torch.hub.load(repo_or_dir='snakers4/silero-models', model='silero_tts', language='ru', speaker=TTS_MODEL_ID, trust_repo=True)
    tts_model.to(device)
    print("TTS model loaded.")
    return tts_model

def load_diarizer_config():
    config_path = Path("diar_infer_telephonic.yaml")
    if not config_path.exists():
        wget.download(DIAR_CONFIG_URL, str(config_path))
    config = OmegaConf.load(config_path)
    config.diarizer.speaker_embeddings.model_path = DIAR_SPEAKER_MODEL
    return config

def load_llm():
    hf_token = os.getenv("HUGGING_FACE_HUB_TOKEN")
    pipeline = transformers.pipeline(
    "text-generation",
    model=LLM_NAME,
    model_kwargs={"torch_dtype": torch.bfloat16},
    device_map="cuda",
    token=hf_token
    )
    return pipeline

llm_model = load_llm()
asr_model = load_asr_model()
(vad_model, vad_utils, vad_iterator) = load_silero_vad_model()
tts_model = load_tts_model()