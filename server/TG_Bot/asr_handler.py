import asyncio
import io

from config.load_models import asr_model

TRANSCRIBE_SEMAPHORE = asyncio.Semaphore(6)

async def transcribe_audio_async(audio_bytes: bytes) -> str:
    loop = asyncio.get_event_loop()

    def _sync_transcribe():
        with io.BytesIO(audio_bytes) as audio_stream:
            segments, _ = asr_model.transcribe(audio_stream, beam_size=3, best_of=1, condition_on_previous_text=False, vad_filter=False, language="ru")
            return " ".join(segment.text.strip() for segment in segments)

    return await loop.run_in_executor(None, _sync_transcribe)
