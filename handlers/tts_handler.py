import io
import soundfile as sf
from config.config import TTS_SPEAKER, TTS_SAMPLE_RATE
from config.load_models import tts_model
import simpleaudio as sa

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

def play_audio_bytes(audio_bytes: bytes):
        """
        Воспроизводит аудиоданные из байтов WAV локально.
        """
        try:
            wave_obj = sa.WaveObject.from_wave_file(io.BytesIO(audio_bytes))
            play_obj = wave_obj.play()
            play_obj.wait_done()
        except Exception as e:
            print(f"Ошибка при воспроизведении аудио локально: {e}")