# api/diarization_handler.py
import nemo.collections.asr as nemo_asr
from omegaconf import OmegaConf
import wget
import os
import json
import subprocess
from pathlib import Path
from config import BASE_DIR, DIAR_CONFIG_URL, DIAR_SPEAKER_MODEL

diarizer_model = None

def load_diarizer_model():
    global diarizer_model
    if diarizer_model is None:
        print("Loading Diarization model...")
        config_path = BASE_DIR / "diar_infer_telephonic.yaml"
        if not config_path.exists():
            wget.download(DIAR_CONFIG_URL, str(config_path))
        config = OmegaConf.load(config_path)
        config.diarizer.speaker_embeddings.model_path = DIAR_SPEAKER_MODEL
        diarizer_model = nemo_asr.models.ClusteringDiarizer(cfg=config)
        print("Diarization model loaded.")

def run_diarization(audio_file_path: str, output_dir: str, num_speakers: int = 0) -> str:
    if diarizer_model is None: raise RuntimeError("Diarizer model not loaded.")
    
    manifest_path = os.path.join(output_dir, "diar_manifest.json")
    meta = {
        'audio_filepath': os.path.abspath(audio_file_path),
        'offset': 0, 'duration': None, 'label': 'infer', 'text': '-',
        'num_speakers': num_speakers if num_speakers > 0 else None, 
        'rttm_filepath': None, 'uem_filepath': None
    }
    with open(manifest_path, 'w', encoding='utf-8') as fp:
        json.dump(meta, fp)
        fp.write('\n')
    
    diarizer_model.cfg.diarizer.manifest_filepath = manifest_path
    diarizer_model.cfg.diarizer.out_dir = output_dir
    diarizer_model.diarize()
    
    rttm_file_path = list(Path(output_dir).rglob('*.rttm'))[0]
    return str(rttm_file_path)

def process_rttm_and_transcribe(rttm_path: str, audio_path: str) -> str:
    # Используем локальный импорт, чтобы избежать циклических зависимостей при запуске
    from api.stt_handler import transcribe_file
    
    with open(rttm_path, 'r') as f:
        lines = f.readlines()

    segments_dir = Path(rttm_path).parent / "segments"
    segments_dir.mkdir(exist_ok=True)
    
    segments = []
    for line in lines:
        parts = line.strip().split()
        start, duration, speaker = float(parts[3]), float(parts[4]), parts[7]
        segment_path = segments_dir / f"{start:.3f}_{speaker}.wav"
        
        command = ['ffmpeg', '-y', '-i', audio_path, '-ss', str(start), '-t', str(duration), '-c', 'copy', str(segment_path)]
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        segments.append({'speaker': speaker, 'path': str(segment_path), 'start': start})

    segments.sort(key=lambda x: x['start'])
    
    transcriptions = transcribe_file([s['path'] for s in segments])
    
    full_dialogue = []
    for i, segment in enumerate(segments):
        text = transcriptions[i].text if i < len(transcriptions) else ""
        full_dialogue.append(f"[{segment['speaker']}]: {text}")
        
    return "\n".join(full_dialogue)
