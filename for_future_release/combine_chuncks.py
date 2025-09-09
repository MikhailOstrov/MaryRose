'''
import glob
import os
import logging
import soundfile as sf
import numpy as np

logger = logging.getLogger(__name__)

def combine_audio_chunks(output_dir, stream_sample_rate, meeting_id, output_filename, pattern="chunk_*.wav"):

    output_filepath = output_dir / output_filename
    all_chunks = sorted(glob.glob(os.path.join(output_dir, "*.wav")))
    full_audio = []

    print(f"Найдено {len(all_chunks)} фрагментов. Объединение...")

    data, rate = sf.read(all_chunks[0], dtype='float32')
    full_audio.append(data)
    for chunk in all_chunks[1:]:
        data, _ = sf.read(chunk, dtype='float32')
        full_audio.append(data)

    combined_audio = np.concatenate(full_audio)

    sf.write(output_filepath, combined_audio, stream_sample_rate)
    logger.info(f"[{meeting_id}] Все аудиофрагменты успешно объединены в: '{output_filepath}'")

    # Сохранение аудиочанков
    def _save_chunk(self, audio_np):
        """Сохраняет аудио-чанк в файл WAV."""
        if audio_np.size == 0:
            return
        filename = f'chunk_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{uuid4().hex[:6]}.wav'
        file_path = self.output_dir / filename
        try:
            sf.write(file_path, audio_np, STREAM_SAMPLE_RATE)
            logger.info(f"💾 Фрагмент сохранен: {filename} (длительность: {len(audio_np)/STREAM_SAMPLE_RATE:.2f} сек)")
        except Exception as e:
            logger.error(f"❌ Ошибка при сохранении аудиофрагмента: {e}")
'''