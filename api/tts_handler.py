# api/tts_handler.py
import torch
import io
import soundfile as sf
from config import TTS_MODEL_ID, TTS_SPEAKER, TTS_SAMPLE_RATE

tts_model = None
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

def load_tts_model():
    global tts_model
    if tts_model is None:
        print("Loading Silero TTS model...")
        tts_model, _ = torch.hub.load(repo_or_dir='snakers4/silero-models', model='silero_tts', language='ru', speaker=TTS_MODEL_ID, trust_repo=True)
        tts_model.to(device)
        print("TTS model loaded.")

def synthesize_speech_to_bytes(text: str) -> bytes:
    if tts_model is None: return b""
    try:
        audio_tensor = tts_model.apply_tts(text=text, speaker=TTS_SPEAKER, sample_rate=TTS_SAMPLE_RATE)
        buffer = io.BytesIO()
        sf.write(buffer, audio_tensor.cpu().numpy(), TTS_SAMPLE_RATE, format='WAV')
        buffer.seek(0)
        return buffer.read()
    except Exception as e:
        print(f"Error in TTS synthesis: {e}")
        return b""
