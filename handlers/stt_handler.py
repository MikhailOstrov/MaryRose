import soundfile as sf
import numpy as np
from config.config import STREAM_SAMPLE_RATE
from config.load_models import asr_model
import os
import tempfile

my_corrections = {
    "Гарри": "Мэри",
    "Мари": "Мэри",
    "Мэрии": "Мэри",
    "Мери": "Мэри",
    "Мерe": "Мэри"
}

def post_process_transcript(transcript: str, corrections: dict) -> str:

    for incorrect, correct in corrections.items():
        transcript = transcript.replace(incorrect, correct)
    return transcript

def transcribe_chunk(audio_chunk_bytes: bytes):
    temp_file_path = None
    try:
        audio_np = np.frombuffer(audio_chunk_bytes, dtype=np.int16)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            temp_file_path = tmp.name
            sf.write(temp_file_path, audio_np, STREAM_SAMPLE_RATE)

        transcriptions = asr_model.transcribe([temp_file_path], batch_size=1)
        raw_transcript = transcriptions[0].text if transcriptions else ""

        corrected_transcript = post_process_transcript(raw_transcript, my_corrections)

        word = "Мэри"
        if corrected_transcript.lower().startswith(word.lower()):
            return corrected_transcript, 1
        else:
            return corrected_transcript, 0

    except Exception as e:
        print(f"Error in transcribe_chunk: {e}")
        return "", 0
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)

def transcribe_file(file_path: str) -> list:
    try:
        return asr_model.transcribe([file_path], batch_size=16)
    except Exception as e:
        print(f"Error in transcribe_file: {e}")
        return []

