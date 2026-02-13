import sys
import os
import time
from unittest.mock import MagicMock

# --- ФАЗА 1: ПРИНУДИТЕЛЬНОЕ ЗАТИРАНИЕ МОДУЛЕЙ ---
# Мы вручную забиваем sys.modules заглушками для ВСЕХ библиотек из requirements.txt
# Теперь Python даже не пойдет искать их на диске.

def force_mock(module_names):
    for name in module_names:
        mock = MagicMock()
        # Чтобы работал импорт типа 'from x import y'
        sys.modules[name] = mock
        # Чтобы работал импорт подмодулей типа 'import x.y'
        sys.modules[f"{name}.version"] = mock
        sys.modules[f"{name}.hub"] = mock

heavy_libs = [
    'huggingface_hub', 'torch', 'openai', 'faster_whisper', 'webrtcvad', 
    'pydub', 'soundfile', 'librosa', 'numpy', 'omegaconf', 'transformers', 
    'sounddevice', 'scipy', 'PIL', 'pyaudio', 'silero_vad', 'requests', 
    'dotenv', 'packaging', 'Cython', 'simpleaudio', 'fastapi', 'uvicorn',
    'pydantic', 'aiohttp', 'websockets', 'python-multipart'
]

force_mock(heavy_libs)

# Фикс для distutils только на Python 3.12+ (там distutils удалён).
# На 3.11 НЕ мокаем — undetected_chromedriver использует LooseVersion для URL скачивания.
if sys.version_info >= (3, 12):
    distutils_mock = MagicMock()
    sys.modules['distutils'] = distutils_mock
    sys.modules['distutils.version'] = MagicMock()

print("🛡️ [SYSTEM] Все тяжелые зависимости заглушены в памяти.")

# --- ФАЗА 2: НАСТРОЙКА ПУТЕЙ ПРОЕКТА ---
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# --- ФАЗА 3: СПЕЦИАЛЬНЫЕ ЗАГЛУШКИ ДЛЯ ТВОЕГО КОДА ---

# Заглушка для AudioHandler
class MockAudioHandler:
    def __init__(self, *args, **kwargs):
        print("✅ [MOCK] AudioHandler создан.")
        self.is_running = kwargs.get('is_running', MagicMock())
    def _process_audio_stream(self):
        while self.is_running.is_set(): time.sleep(1)
    def _perform_post_processing(self): pass

# Прописываем заглушку ТАМ, где её ищет meet_listener.py
mock_handler_mod = MagicMock()
mock_handler_mod.AudioHandler = MockAudioHandler
sys.modules['handlers.audio_handler'] = mock_handler_mod

# Заглушка для VirtualAudioManager
class MockVirtualAudioManager:
    def __init__(self, meeting_id):
        self.sink_name = self.source_name = self.monitor_name = "mock"
    def create_devices(self): return True
    def destroy_devices(self): pass

mock_manager_mod = MagicMock()
mock_manager_mod.VirtualAudioManager = MockVirtualAudioManager
sys.modules['api.audio_manager'] = mock_manager_mod

# Заглушка для parec
import subprocess
original_popen = subprocess.Popen
def mocked_popen(*args, **kwargs):
    if args and any('parec' in str(arg) for arg in args[0]):
        return MagicMock(stdout=MagicMock(read=lambda n: b'\x00'*n), poll=lambda: None)
    return original_popen(*args, **kwargs)
subprocess.Popen = mocked_popen

# --- ФАЗА 4: ЗАПУСК ---

def run_debug_session():
    # Логирование сразу — иначе logs/app.log не создаётся до ввода ссылки
    from config.logging import setup_logging
    setup_logging()

    print("\n🚀 STARTING DEBUG SESSION (Selenium Only Mode) 🚀")
    
    if not os.getenv("CHROMEDRIVER_PATH"):
        os.environ["CHROMEDRIVER_PATH"] = "auto"
    
    meeting_url = input("🔗 Вставьте ссылку на Meet: ").strip()
    if not meeting_url: return

    try:
        # Теперь когда sys.modules забит заглушками, импорт ОБЯЗАН сработать
        from api.meet_listener import MeetListenerBot
    except Exception as e:
        print(f"❌ Ошибка импорта: {e}")
        import traceback
        traceback.print_exc()
        return

    bot = MeetListenerBot(
        meeting_url=meeting_url,
        meeting_id="debug_session",
        email="test@test.com",
        remaining_seconds=3600
    )

    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n🛑 Останавливаю...")
        bot.stop()
    except Exception as e:
        print(f"❌ Ошибка в рантайме: {e}")
        bot.stop()

if __name__ == "__main__":
    run_debug_session()
