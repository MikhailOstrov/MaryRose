import glob
import os
import logging
import soundfile as sf
import numpy as np

logger = logging.getLogger(__name__)

'''

Пока не используем. Это на будущее, чтобы конвертировать разные форматы аудио

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
        raise
'''
def combine_audio_chunks(output_dir, stream_sample_rate, meeting_id, output_filename):
    """
    Соединяет все аудиофрагменты из указанной директории в один WAV-файл.

    Args:
        output_dir (pathlib.Path или str): Путь к директории, где хранятся аудиофрагменты.
        stream_sample_rate (int): Частота дискретизации аудиофрагментов.
        meeting_id (str): ID встречи, используется для логирования.
        output_filename (str): Имя файла для сохранения объединенного аудио.
    """

    output_filepath = output_dir / output_filename
    
    audio_files = sorted(glob.glob(os.path.join(output_dir, "*.wav")))
    
    if not audio_files:
        logger.warning(f"[{meeting_id}] В директории '{output_dir}' не найдено аудиофайлов для объединения.")
        return

    combined_audio_data = []

    logger.info(f"[{meeting_id}] Начинаем объединение {len(audio_files)} аудиофрагментов...")

    for file_path in audio_files:
        data, _ = sf.read(file_path) 
        combined_audio_data.append(data)

    final_audio_array = np.concatenate(combined_audio_data)

    sf.write(output_filepath, final_audio_array, stream_sample_rate)
    logger.info(f"[{meeting_id}] Все аудиофрагменты успешно объединены в: '{output_filepath}'")
