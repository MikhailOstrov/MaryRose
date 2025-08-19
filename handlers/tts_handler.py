import io
import soundfile as sf
from config.config import TTS_SPEAKER, TTS_SAMPLE_RATE
import simpleaudio as sa

# Перевод текста в аудио (пока не работает)
def synthesize_speech_to_bytes(text: str, tts_model) -> bytes:
    """
    Синтезирует речь, используя ПЕРЕДАННЫЙ экземпляр TTS-модели.
    """
    try:
        audio_tensor = tts_model.apply_tts(text=text, speaker=TTS_SPEAKER, sample_rate=TTS_SAMPLE_RATE)
        buffer = io.BytesIO()
        sf.write(buffer, audio_tensor.cpu().numpy(), TTS_SAMPLE_RATE, format='WAV')
        buffer.seek(0)
        print("Речь сгенерирована")
        return buffer.read()
    except Exception as e:
        print(f"Ошибка в синтезе TTS: {e}", exc_info=True)
        return b""