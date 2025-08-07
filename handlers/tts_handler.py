import io
import soundfile as sf
from config.config import TTS_SPEAKER, TTS_SAMPLE_RATE
from config.load_models import tts_model
import simpleaudio as sa

# Перевод текста в аудио (пока не работает)
def synthesize_speech_to_bytes(text: str) -> bytes:
    try:
        audio_tensor = tts_model.apply_tts(text=text, speaker=TTS_SPEAKER, sample_rate=TTS_SAMPLE_RATE)
        buffer = io.BytesIO()
        sf.write(buffer, audio_tensor.cpu().numpy(), TTS_SAMPLE_RATE, format='WAV')
        buffer.seek(0)
        return buffer.read()
    except Exception as e:
        print(f"Error in TTS synthesis: {e}")
        return b""