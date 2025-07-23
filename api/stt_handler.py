import nemo.collections.asr as nemo_asr
import torch
import soundfile as sf
import numpy as np
from config import ASR_MODEL_NAME, STREAM_SAMPLE_RATE

asr_model = None
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

def load_asr_model():
    global asr_model
    if asr_model is None:
        print("Loading NeMo ASR model...")
        asr_model = nemo_asr.models.EncDecHybridRNNTCTCBPEModel.from_pretrained(model_name=ASR_MODEL_NAME).to(device)
        print("ASR model loaded.")

def transcribe_chunk(audio_chunk_bytes: bytes) -> str:
    if asr_model is None: return ""
    try:
        audio_np = np.frombuffer(audio_chunk_bytes, dtype=np.int16)
        temp_file = "temp_stream_chunk.wav"
        sf.write(temp_file, audio_np, STREAM_SAMPLE_RATE)
        transcriptions = asr_model.transcribe([temp_file], batch_size=1)
        return transcriptions[0].text if transcriptions else ""
    except Exception as e:
        print(f"Error in transcribe_chunk: {e}")
        return ""

def transcribe_file(file_path: str) -> list:
    if asr_model is None: return []
    try:
        return asr_model.transcribe([file_path], batch_size=16)
    except Exception as e:
        print(f"Error in transcribe_file: {e}")
        return []

