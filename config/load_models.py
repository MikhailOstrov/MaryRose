import os

os.environ['HOME'] = '/app'
os.environ['TORCH_HOME'] = '/app/.cache/torch'
os.environ['NEMO_CACHE_DIR'] = '/app/.cache/nemo'

import nemo.collections.asr as nemo_asr
from omegaconf import OmegaConf
import torch
import wget
from pathlib import Path
from .config import ASR_MODEL_NAME, TTS_MODEL_ID, DIAR_SPEAKER_MODEL, DIAR_CONFIG_URL

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

def load_asr_model():
    asr_model = nemo_asr.models.EncDecHybridRNNTCTCBPEModel.from_pretrained(model_name=ASR_MODEL_NAME).to(device)
    print("ASR model loaded.")
    return asr_model

def load_silero_vad_model():
    model, utils = torch.hub.load(repo_or_dir='snakers4/silero-vad',
                                  model='silero_vad',
                                  force_reload=True,
                                  onnx=True)
    print("Silero VAD model loaded.")
    return model, utils

def load_tts_model():
    tts_model, _ = torch.hub.load(repo_or_dir='snakers4/silero-models', model='silero_tts', language='ru', speaker=TTS_MODEL_ID, trust_repo=True)
    tts_model.to(device)
    print("TTS model loaded.")
    return tts_model

def load_diarizer_model():
    config_path = Path("diar_infer_telephonic.yaml")
    if not config_path.exists():
        wget.download(DIAR_CONFIG_URL, str(config_path))
    config = OmegaConf.load(config_path)
    config.diarizer.speaker_embeddings.model_path = DIAR_SPEAKER_MODEL
    diarizer_model = nemo_asr.models.ClusteringDiarizer(cfg=config)
    print("Diarization model loaded.")
    return diarizer_model

asr_model = load_asr_model()
(vad_model, vad_utils) = load_silero_vad_model()
tts_model = load_tts_model()
diarizer_model = load_diarizer_model()