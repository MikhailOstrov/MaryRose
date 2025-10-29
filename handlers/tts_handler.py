import soundfile as sf
from config.load_models import tts_model
import io 

def generate_audio(text):
    sample_rate=24000
    audio = tts_model.apply_tts(
        text=text,
        speaker='kseniya',
        sample_rate=sample_rate,
        put_accent=True,
        put_yo=True
    )
    buffer = io.BytesIO()
    sf.write(buffer, audio.cpu().numpy(), sample_rate, format='WAV')
    buffer.seek(0)
    audio_bytes = buffer.read()
    return audio_bytes