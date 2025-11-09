import asyncio
import io
import soundfile as sf

from config.load_models import asr_model, te_model

TRANSCRIBE_SEMAPHORE = asyncio.Semaphore(6)

async def transcribe_audio_async(audio_path: str) -> str:
    loop = asyncio.get_event_loop()

    def split_audio_into_chunks(chunk_duration=29, sample_rate=16000):
        try:
            # Читаем аудиофайл
            audio_data, sr = sf.read(audio_path)
            
            samples_per_chunk = chunk_duration * sample_rate
            total_samples = len(audio_data)
            
            # Создаем списки для хранения путей к чанкам и их длительностей
            chunk_paths = []
            chunk_durations = []  # Новый список для длительностей в секундах
            
            # Разделяем аудио на чанки
            for i, start_sample in enumerate(range(0, total_samples, samples_per_chunk)):
                end_sample = min(start_sample + samples_per_chunk, total_samples)
                chunk_data = audio_data[start_sample:end_sample]
                
                # Пропускаем пустые чанки
                if len(chunk_data) == 0:
                    continue
                
                # Создаем временный файл для чанка
                chunk_path = audio_path.parent / f"chunk_{i:04d}.wav"
                sf.write(chunk_path, chunk_data, sample_rate, subtype='PCM_16')
                chunk_paths.append(chunk_path)
                
                # Рассчитываем длительность чанка
                chunk_duration_sec = len(chunk_data) / sample_rate
                chunk_durations.append(chunk_duration_sec)
                
                print(f"Создан чанк {i+1}: {chunk_path} ({chunk_duration_sec:.2f} сек)")
            
            print(f"Всего создано {len(chunk_paths)} чанков")
            return chunk_paths, chunk_durations  # Возвращаем оба списка
            
        except Exception as e:
            print(f"Ошибка при разделении аудио: {e}")
            return [], []

    def perform_post_processing():
        
        # Получаем чанки и их длительности
        chunk_paths, chunk_durations = split_audio_into_chunks()
    
        
        full_text_parts = []
        current_offset = 0.0  
        
        try:
            for idx, chunk in enumerate(chunk_paths):
                if idx >= len(chunk_durations):
                    break
                
                chunk_duration = chunk_durations[idx]
                
                # Распознавание: возвращает только текст
                transcription = asr_model.recognize(chunk)
                transcription_te = te_model(transcription, lan='ru')
                # Абсолютное время для всего чанка
                absolute_start = current_offset
                absolute_end = current_offset + chunk_duration
                
                # Формируем строку для этого чанка
                start_str = format_time_hms(absolute_start)
                end_str = format_time_hms(absolute_end)
                full_text_parts.append(f"[{start_str} - {end_str}] {transcription_te.strip()}")
                
                # Обновляем offset для следующего чанка
                current_offset += chunk_duration
            
            # Собираем полный текст
            full_text = "\n".join(full_text_parts)
            
        finally:
            # Опционально: удаляем временные чанки
            for path in chunk_paths:
                try:
                    path.unlink()
                except:
                    pass
        return full_text

    def format_time_hms(self, seconds: float) -> str:
        """Перевод секунд в формат HH:MM:SS"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
            
    return await loop.run_in_executor(None, perform_post_processing)
