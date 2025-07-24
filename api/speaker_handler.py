# api/speaker_handler.py
import nemo.collections.asr as nemo_asr
import torch
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from config import SPEAKER_MODEL_NAME, VERIFICATION_THRESHOLD, USER_DATA_DIR, STREAM_SAMPLE_RATE

speaker_model = None
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

def load_speaker_model():
    global speaker_model
    if speaker_model is None:
        print(f"Loading Speaker Recognition model ({SPEAKER_MODEL_NAME})...")
        speaker_model = nemo_asr.models.EncDecSpeakerLabelModel.from_pretrained(model_name=SPEAKER_MODEL_NAME).to(device).eval()
        print("Speaker Recognition model loaded.")

def create_voiceprint(user_id: str, audio_files: list) -> bool:
    if speaker_model is None: raise RuntimeError("Speaker model not loaded.")
    try:
        embeddings = [speaker_model.get_embedding(file).squeeze().cpu().numpy() for file in audio_files]
        voiceprint = np.mean(embeddings, axis=0)
        user_dir = USER_DATA_DIR / user_id
        user_dir.mkdir(exist_ok=True)
        np.save(user_dir / "voiceprint.npy", voiceprint)
        return True
    except Exception as e:
        print(f"Error creating voiceprint for {user_id}: {e}")
        return False

def verify_speaker(user_id: str, audio_chunk_bytes: bytes) -> bool:
    if speaker_model is None: return False
    try:
        voiceprint_path = USER_DATA_DIR / user_id / "voiceprint.npy"
        if not voiceprint_path.exists(): return False
        
        registered_voiceprint = np.load(voiceprint_path)
        audio_np = np.frombuffer(audio_chunk_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        
        # Убедимся, что аудио достаточно длинное для анализа
        if len(audio_np) < STREAM_SAMPLE_RATE * 0.5: # Минимум 0.5 секунды
            return False

        current_embedding = speaker_model.get_embedding_from_audio(audio_np).squeeze().cpu().numpy()
        similarity = cosine_similarity(registered_voiceprint.reshape(1, -1), current_embedding.reshape(1, -1))[0][0]
        return similarity > VERIFICATION_THRESHOLD
    except Exception:
        return False
