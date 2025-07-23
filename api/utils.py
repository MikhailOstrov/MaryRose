# api/utils.py
import subprocess
from pathlib import Path
from config import UPLOADS_DIR

def convert_to_standard_wav(input_path: Path) -> Path:
    """
    Конвертирует любой аудиофайл в стандартный для NeMo формат:
    WAV, 16000 Гц, моно, 16-bit PCM.
    Возвращает путь к новому сконвертированному файлу.
    """
    output_filename = f"{input_path.stem}_16k_mono.wav"
    output_path = UPLOADS_DIR / output_filename
    
    print(f"Converting {input_path.name} to standard WAV format...")

    command = [
        'ffmpeg',
        '-y',                   # Перезаписывать файл без вопроса
        '-i', str(input_path),  # Входной файл
        '-ar', '16000',         # Частота дискретизации 16кГц
        '-ac', '1',             # 1 аудиоканал (моно)
        '-c:a', 'pcm_s16le',    # Кодек: 16-bit PCM
        str(output_path)
    ]

    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(f"Conversion successful. File saved to: {output_path}")
        return output_path
    except subprocess.CalledProcessError as e:
        print(f"FFmpeg conversion error: {e.stderr.decode()}")
        raise  # Передаем исключение выше, чтобы обработать его в main.py

