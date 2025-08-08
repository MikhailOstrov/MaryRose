import os
import time
import queue
import threading
import logging
import requests
from datetime import datetime
from uuid import uuid4
import torch
import numpy as np
from scipy.io.wavfile import write
import sounddevice as sd
import soundfile as sf
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from config.config import (STREAM_SAMPLE_RATE,SILENCE_THRESHOLD_FRAMES, MEET_FRAME_DURATION_MS,
                           MEET_AUDIO_CHUNKS_DIR, MEET_INPUT_DEVICE_NAME, STREAM_TRIGGER_WORD, CHROME_PROFILE_DIR,
                           MEET_GUEST_NAME, SUMMARY_OUTPUT_DIR, STREAM_STOP_WORD_1, STREAM_STOP_WORD_2, STREAM_STOP_WORD_3)
from handlers.ollama_handler import get_mary_response, get_summary_response, get_title_response
from handlers.tts_handler import synthesize_speech_to_bytes
from handlers.diarization_handler import run_diarization, process_rttm_and_transcribe
from config.load_models import vad_model, asr_model
from api.utils import combine_audio_chunks
from api.audio_routing import create_virtual_audio_pair, unload_module, wait_and_route_new_streams, ensure_routing

logger = logging.getLogger(__name__)

class MeetListenerBot:

    # Определение атрибутов класса
    def __init__(self, meeting_url: str, meeting_id: str):

        self.meeting_url = meeting_url # Ссылка на Google Meet
        self.meeting_id = meeting_id # ID для отслеживания сессии
        self.driver = None 
        self.audio_queue = queue.Queue() # Для аудиопотока

        self.is_running = threading.Event()
        self.is_running.set()

        self.vad = vad_model # VAD-модель (from config.load_models import vad_model)
        self.asr_model = asr_model # Whisper (from config.load_models import asr_model)
        self.summary_output_dir = SUMMARY_OUTPUT_DIR # Директория сохранения summary
        self.joined_successfully = False 

        self.frame_size = int(STREAM_SAMPLE_RATE * MEET_FRAME_DURATION_MS / 1000) # Для VAD-модели (длительность чанка)
        self.silent_frames_threshold = SILENCE_THRESHOLD_FRAMES # Пауза в речи в сек.

        self.output_dir = MEET_AUDIO_CHUNKS_DIR / self.meeting_id # Папка для сохранения чанков и скриншотов
        os.makedirs(self.output_dir, exist_ok=True)
        logger.info(f"[{self.meeting_id}] Аудиофрагменты будут сохраняться в: '{self.output_dir}'")

        # Per-meeting виртуальные устройства PulseAudio
        self.meet_sink_name = None
        self.meet_mic_name = None
        self.meet_sink_module_id = None
        self.meet_mic_module_id = None

        self.bot_sink_name = None
        self.bot_mic_name = None
        self.bot_sink_module_id = None
        self.bot_mic_module_id = None
        # Управление автоозвучкой
        self.enable_auto_tts = True

    # Отслеживание кол-ва участников
    def _monitor_participants(self):
        """Отслеживает количество участников. Если бот остается один, он завершает работу."""
        threading.current_thread().name = f'ParticipantMonitor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Мониторинг участников запущен.")
        
        participant_locator_xpath = "//button[.//i[text()='people'] and @aria-label]"
        consecutive_failures = 0
        max_failures = 2

        while self.is_running.is_set():
            for _ in range(15): # Проверяем каждые 15 секунд
                if not self.is_running.is_set():
                    logger.info(f"[{self.meeting_id}] Мониторинг участников остановлен.")
                    return
                time.sleep(1)
            
            try:
                participant_element = self.driver.find_element(By.XPATH, participant_locator_xpath)
                aria_label = participant_element.get_attribute('aria-label') or ""
                numbers = ''.join(filter(str.isdigit, aria_label))
                if numbers:
                    count = int(numbers)
                    logger.info(f"[{self.meeting_id}] Текущее количество участников: {count}")
                    consecutive_failures = 0 # Сбрасываем счетчик при успехе
                    if count <= 1:
                        logger.warning(f"[{self.meeting_id}] Встреча пуста. Завершаю работу...")
                        self.stop()
                        return
                else:
                    # Это может произойти, если элемент найден, но в нем нет цифр
                    consecutive_failures += 1
                    logger.warning(f"[{self.meeting_id}] Не удалось извлечь число участников из элемента. Попытка {consecutive_failures}/{max_failures}.")

            except Exception:
                consecutive_failures += 1
                logger.warning(f"[{self.meeting_id}] Не удалось найти счетчик участников. Попытка {consecutive_failures}/{max_failures}.")

            if consecutive_failures >= max_failures:
                logger.error(f"[{self.meeting_id}] Не удалось найти счетчик участников {max_failures} раз подряд. Предполагаю, что встреча завершена.")
                self.stop()
                return
    
    # Инициализация драйвера для подключения
    def _initialize_driver(self):
        logger.info(f"[{self.meeting_id}] Запуск undetected_chromedriver с настройками из join_meet...")
        try:
            logger.info(f"[{self.meeting_id}] Попытка №1: с user-data-dir и use_subprocess=True")
            opt = uc.ChromeOptions()
            opt.add_argument('--no-sandbox')
            opt.add_argument('--disable-dev-shm-usage')
            opt.add_argument(f'--user-data-dir={CHROME_PROFILE_DIR}') 
            
            self.driver = uc.Chrome(
                options=opt,
                headless=False, # Важно для работы в Xvfb
                use_subprocess=True, # Важно для стабильности
                version_main=138 # Закрепляем версию для надежности
            )
            logger.info(f"[{self.meeting_id}] ✅ Chrome запущен (Попытка №1)!")
            
        except Exception as e:
            logger.error(f"[{self.meeting_id}] Попытка №1 не сработала: {e}")
            logger.info(f"[{self.meeting_id}] Попытка №2: с базовыми опциями...")
            try:
                opt = uc.ChromeOptions()
                opt.add_argument('--no-sandbox')
                opt.add_argument('--disable-dev-shm-usage')
                opt.add_argument('--disable-gpu')
                opt.add_argument(f'--user-data-dir={CHROME_PROFILE_DIR}')
                opt.add_argument('--window-size=1280,720')
                
                opt.add_experimental_option("prefs", {
                    "profile.default_content_setting_values.media_stream_mic": 1,
                    "profile.default_content_setting_values.notifications": 2
                })
                
                self.driver = uc.Chrome(options=opt, version_main=138)
                logger.info(f"[{self.meeting_id}] ✅ Chrome запущен (Попытка №2)!")
                
            except Exception as e2:
                logger.critical(f"[{self.meeting_id}] Полный провал запуска Chrome: {e2}", exc_info=True)
                raise
    
    # Скриншот для отладки 
    def _save_screenshot(self, name: str):
        """Сохраняет скриншот для отладки."""
        path = self.output_dir / f'{datetime.now().strftime("%H%M%S")}_{name}.png'
        try:
            self.driver.save_screenshot(str(path))
            logger.info(f"[{self.meeting_id}] Скриншот сохранен: {path}")
        except Exception as e:
            logger.warning(f"[{self.meeting_id}] Не удалось сохранить скриншот '{name}': {e}")

    def _handle_mic_dialog(self):
        """
        Универсальная обработка диалога выбора микрофона.
        Сначала пытается выбрать вариант с микрофоном (RU/EN) по нескольким шаблонам.
        Если не удалось найти — пробует вариант без микрофона как фолбэк.
        """
        with_mic_variants = [
            "Use microphone",
            "Join with microphone",
            "Use your microphone",
            "Продолжить с микрофоном",
            "Использовать микрофон",
            "Войти с микрофоном",
        ]
        without_mic_variants = [
            "Continue without microphone",
            "Join without microphone",
            "Продолжить без микрофона",
            "Без микрофона",
        ]

        def try_click_by_phrases(phrases, timeout_each=6):
            for phrase in phrases:
                xpaths = [
                    f"//button[normalize-space()='{phrase}']",
                    f"//button[contains(., '{phrase}')]",
                    f"//span[normalize-space()='{phrase}']/ancestor::button",
                    f"//span[contains(., '{phrase}')]/ancestor::button",
                    f"//div[@role='button' and normalize-space()='{phrase}']",
                    f"//div[@role='button' and contains(., '{phrase}')]",
                    f"//*[@role='button' and .//span[normalize-space()='{phrase}']]",
                ]
                clicked = False
                for xp in xpaths:
                    try:
                        btn = WebDriverWait(self.driver, timeout_each).until(
                            EC.element_to_be_clickable((By.XPATH, xp))
                        )
                        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                        btn.click()
                        clicked = True
                        logger.info(f"[{self.meeting_id}] Нажал кнопку по XPath: {xp}")
                        return True
                    except Exception:
                        continue
                if not clicked:
                    # JS fallback по innerText
                    try:
                        js = """
                        const phrase = arguments[0].toLowerCase();
                        const nodes = Array.from(document.querySelectorAll('button, div[role="button"]'));
                        for (const n of nodes) {
                          const t = (n.innerText||'').trim().toLowerCase();
                          if (t.includes(phrase)) { n.scrollIntoView({block:'center'}); n.click(); return true; }
                        }
                        return false;
                        """
                        ok = self.driver.execute_script(js, phrase)
                        if ok:
                            logger.info(f"[{self.meeting_id}] Нажал кнопку через JS: '{phrase}'")
                            return True
                    except Exception:
                        pass
            return False

        if try_click_by_phrases(with_mic_variants, timeout_each=3):
            time.sleep(1)
            self._save_screenshot("02a_mic_dialog_with_mic")
            return

        if try_click_by_phrases(without_mic_variants, timeout_each=3):
            time.sleep(1)
            self._save_screenshot("02a_mic_dialog_without_mic")
            return

        logger.info(f"[{self.meeting_id}] Диалог микрофона не найден — продолжаю.")

    def _setup_audio_devices(self):
        """Создает пары устройств для текущей встречи (прослушивание и говорение)."""
        if self.meet_sink_name is not None:
            return
        meet_prefix = f"meet_{self.meeting_id}"
        bot_prefix = f"bot_{self.meeting_id}"

        self.meet_sink_name, self.meet_mic_name, self.meet_sink_module_id, self.meet_mic_module_id = create_virtual_audio_pair(meet_prefix)
        self.bot_sink_name, self.bot_mic_name, self.bot_sink_module_id, self.bot_mic_module_id = create_virtual_audio_pair(bot_prefix)
        logger.info(f"[{self.meeting_id}] Созданы устройства: {self.meet_sink_name}/{self.meet_mic_name} и {self.bot_sink_name}/{self.bot_mic_name}")

    def _teardown_audio_devices(self):
        for mid in [self.meet_sink_module_id, self.meet_mic_module_id, self.bot_sink_module_id, self.bot_mic_module_id]:
            if mid is not None:
                unload_module(mid)
        logger.info(f"[{self.meeting_id}] Виртуальные аудиоустройства выгружены")

    def _speak_via_meet(self, text: str):
        """Синтезирует TTS и проигрывает его в bot_sink_<id>, чтобы Meet отправил звук участникам."""
        if not text or not self.bot_sink_name:
            return
        try:
            audio_bytes = synthesize_speech_to_bytes(text)
            if not audio_bytes:
                return
            import subprocess
            subprocess.run(["paplay", f"--device={self.bot_sink_name}", "/dev/stdin"], input=audio_bytes, check=True)
            logger.info(f"[{self.meeting_id}] Озвучен ответ ассистента через {self.bot_sink_name}")
        except Exception as e:
            logger.error(f"[{self.meeting_id}] Ошибка при автоозвучке: {e}")
            
    # Присоединение в Google Meet
    def join_meet_as_guest(self):
        try:
            logger.info(f"[{self.meeting_id}] Подключаюсь к встрече как гость: {self.meeting_url}")
            self.driver.get(self.meeting_url)
            time.sleep(8)
            self._save_screenshot("01_meet_page_loaded")
            
            logger.info(f"[{self.meeting_id}] Ищу поле для ввода имени...")
            name_input_xpath = '//input[@placeholder="Your name" or @aria-label="Your name" or contains(@placeholder, "name")]'
            name_input = WebDriverWait(self.driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, name_input_xpath))
            )
            
            logger.info(f"[{self.meeting_id}] Ввожу имя: {MEET_GUEST_NAME}")
            name_input.clear()
            name_input.send_keys(MEET_GUEST_NAME)
            time.sleep(2)
            self._save_screenshot("02_name_entered")

            logger.info(f"[{self.meeting_id}] Обработка диалога микрофона...")
            self._handle_mic_dialog()
            
            join_button_xpath = '//button[.//span[contains(text(), "Ask to join") or contains(text(), "Попросить войти")]]'
            logger.info(f"[{self.meeting_id}] Ищу кнопку 'Ask to join'...")
            join_button = WebDriverWait(self.driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, join_button_xpath))
            )
            join_button.click()
            self._save_screenshot("03_after_ask_to_join")
            
            logger.info(f"[{self.meeting_id}] Запрос отправлен. Ожидаю одобрения хоста (до 120с)...")
            max_wait_time, check_interval, elapsed_time = 120, 2, 0
            
            # ОБНОВЛЕННЫЙ И НАДЕЖНЫЙ СПИСОК ИНДИКАТОРОВ УСПЕХА
            success_indicators = [
                # Кнопка завершения звонка - самый надежный индикатор
                '//button[@data-tooltip*="end call" or @aria-label*="end call" or @aria-label*="завершить"]',
                # Кнопка списка участников, которая появляется только внутри встречи
                "//button[.//i[text()='people'] and @aria-label]",
                # Другие надежные элементы интерфейса
                '//div[@data-self-name]', # Элемент с именем самого бота
                '//div[contains(@class, "control") and (contains(@class, "bar") or contains(@class, "panel"))]', # Панель управления
                '//button[@aria-label*="hand" or @aria-label*="рука" or @data-tooltip*="hand"]' # Кнопка "поднять руку"
            ]
            # ПОЛНЫЙ СПИСОК ИНДИКАТОРОВ ОШИБКИ
            error_indicators = [
                '//*[contains(text(), "denied") or contains(text(), "отклонен")]',
                '//*[contains(text(), "rejected") or contains(text(), "отказано")]',
                '//*[contains(text(), "error") or contains(text(), "ошибка")]',
                '//*[contains(text(), "unable") or contains(text(), "невозможно")]'
            ]

            while elapsed_time < max_wait_time:
                for i, xpath in enumerate(success_indicators):
                    try:
                        if self.driver.find_element(By.XPATH, xpath).is_displayed():
                            self._save_screenshot("04_joined_successfully")
                            logger.info(f"[{self.meeting_id}] ✅ Успешно присоединился к встрече! (индикатор #{i+1})")
                            # После входа перенаправим новые потоки Chrome на наши устройства
                            moved_sinks, moved_sources = wait_and_route_new_streams(
                                target_meet_sink=self.meet_sink_name,
                                target_bot_mic=self.bot_mic_name,
                                timeout_sec=20.0,
                                poll_interval_sec=0.5,
                            )
                            logger.info(f"[{self.meeting_id}] Перенаправлено потоков Chrome: sinks={moved_sinks}, sources={moved_sources}")
                            self.joined_successfully = True
                            return True
                    except: continue
                
                for error_xpath in error_indicators:
                    try:
                        error_element = self.driver.find_element(By.XPATH, error_xpath)
                        if error_element.is_displayed():
                            logger.error(f"[{self.meeting_id}] ❌ Присоединение отклонено: {error_element.text}")
                            self._save_screenshot("98_join_denied")
                            return False
                    except: continue

                time.sleep(check_interval)
                elapsed_time += check_interval
                if elapsed_time % 30 == 0:
                    logger.info(f"[{self.meeting_id}] Ожидание... {elapsed_time}с прошло.")
                    self._save_screenshot(f"wait_{elapsed_time}s")

            logger.warning(f"[{self.meeting_id}] ⚠️ Превышено время ожидания одобрения ({max_wait_time}с).")
            self._save_screenshot("99_join_timeout")
            return False

        except Exception as e:
            logger.critical(f"[{self.meeting_id}] ❌ Критическая ошибка при присоединении: {e}", exc_info=True)
            self._save_screenshot("99_join_fatal_error")
            return False
    
    # Поиск и определение аудиоустройства
    def _find_device_id(self):
        logger.info(f"[{self.meeting_id}] Поиск аудиоустройства с именем '{MEET_INPUT_DEVICE_NAME}'...")
        try:
            devices = sd.query_devices()
            logger.debug(f"Найденные аудиоустройства: {devices}")
            preferred_names = [self.meet_mic_name, MEET_INPUT_DEVICE_NAME]
            for i, device in enumerate(devices):
                if any(name and name in device['name'] for name in preferred_names) and device['max_input_channels'] > 0:
                    logger.info(f"[{self.meeting_id}] ✅ Найдено целевое устройство: ID {i}, Имя: {device['name']}")
                    return i
            raise ValueError(f"Не удалось найти входное аудиоустройство с именем '{MEET_INPUT_DEVICE_NAME}'")
        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Ошибка при поиске аудиоустройств: {e}", exc_info=True)
            raise

    # Callback функция
    def _audio_capture_callback(self, indata, frames, time, status):
        if status:
            logger.warning(f"[{self.meeting_id}] Статус аудиоустройства: {status}")
        if self.is_running.is_set():
            self.audio_queue.put(bytes(indata))

    # Обработка аудиопотока -- транскрибация -- ответ (если обнаружен триггер)
    def _process_audio_stream(self):
        threading.current_thread().name = f'VADProcessor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Процессор VAD запущен с моделью Silero.")

        vad_buffer = None
        VAD_CHUNK_SIZE = 512
        speech_buffer_for_asr = []
        is_speaking = False
        silent_frames_after_speech = 0

        while self.is_running.is_set():
            # Автоподдержание маршрутизации: если по ходу звонка у Chrome появятся новые потоки,
            # раз в несколько циклов переназначаем их на нужные устройства (идемпотентно).
            try:
                if self.meet_sink_name and self.bot_mic_name:
                    ensure_routing(self.meet_sink_name, self.bot_mic_name)
            except Exception:
                pass
            
            try:
                audio_frame_bytes = self.audio_queue.get(timeout=1)
                if not audio_frame_bytes:
                    continue

                audio_np = np.frombuffer(audio_frame_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                new_audio_tensor = torch.from_numpy(audio_np)

                if vad_buffer is None:
                    vad_buffer = new_audio_tensor
                else:
                    vad_buffer = torch.cat([vad_buffer, new_audio_tensor])

                while vad_buffer is not None and vad_buffer.shape[0] >= VAD_CHUNK_SIZE:
                    
                    chunk_to_process = vad_buffer[:VAD_CHUNK_SIZE]
                    vad_buffer = vad_buffer[VAD_CHUNK_SIZE:]
                    speech_prob = self.vad(chunk_to_process, STREAM_SAMPLE_RATE).item()
                    
                    if speech_prob > 0.3:
                        if not is_speaking:
                            logger.info(f"[{self.meeting_id}] Обнаружено начало речи.")
                            is_speaking = True
                        
                        speech_buffer_for_asr.append(chunk_to_process.numpy())
                        silent_frames_after_speech = 0
                    else:
                        if is_speaking:
                            
                            silent_frames_after_speech += 1
                            
                            if silent_frames_after_speech > self.silent_frames_threshold:
                                logger.info(f"[{self.meeting_id}] Обнаружен конец фразы.")
                                is_speaking = False
                                silent_frames_after_speech = 0
                                
                                if speech_buffer_for_asr:
                                    full_audio_np = np.concatenate(speech_buffer_for_asr)
                                    speech_buffer_for_asr = []
                                    
                                    self._save_chunk(full_audio_np)

                                    segments, _ = self.asr_model.transcribe(full_audio_np, beam_size=5, language="ru")
                                    transcription = "".join([seg.text for seg in segments]).strip()
                                    print(f"Распознано: {transcription}")

                                    if transcription.lower().lstrip().startswith(STREAM_TRIGGER_WORD):

                                        clean_transcription = ''.join(char for char in transcription.lower() if char.isalnum() or char.isspace())
                                        print(clean_transcription)

                                        if STREAM_STOP_WORD_1 in clean_transcription or STREAM_STOP_WORD_2 in clean_transcription or STREAM_STOP_WORD_3 in clean_transcription:
                                            logger.info(f"[{self.meeting_id}] Провожу постобработку и завершаю работу")
                                            self.stop()
                                        else:
                                            logger.info(f"[{self.meeting_id}] Мэри услышала вас")
                                            response = get_mary_response(transcription)
                                            logger.info(f"[{self.meeting_id}] Ответ от Мэри: {response}")
                                            if self.enable_auto_tts:
                                                self._speak_via_meet(response)
            except queue.Empty:
                if is_speaking and speech_buffer_for_asr:
                    logger.info(f"[{self.meeting_id}] Тайм-аут, обрабатываем оставшуюся речь.")
                    is_speaking = False
                continue
            except Exception as e:
                logger.error(f"[{self.meeting_id}] Ошибка в цикле VAD: {e}", exc_info=True)

    # Постобработка: объединение аудиочанков -- запуск диаризации и объединение с транскрибацией -- суммаризация -- генерация заголовка -- отправка результатов на внешний сервер
    def _perform_post_processing(self):
        """
        Выполняет всю постобработку: объединение аудио, транскрипцию,
        диаризацию и суммаризацию. Вызывается в отдельном потоке.
        """
        threading.current_thread().name = f'PostProcessor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Начинаю постобработку...")

        try:
            # Объединение аудио чанков
            combined_audio_filename = f"combined_meeting_{self.meeting_id}.wav"
            combined_audio_filepath = self.output_dir / combined_audio_filename

            combine_audio_chunks(
                output_dir=self.output_dir,
                stream_sample_rate=STREAM_SAMPLE_RATE,
                meeting_id=self.meeting_id,
                output_filename=combined_audio_filename,
                pattern="chunk_*.wav"
            )
            
            if not os.path.exists(combined_audio_filepath):
                logger.error(f"[{self.meeting_id}] Объединенный аудиофайл не был создан: {combined_audio_filepath}")
                return
            
            # Диаризация
            logger.info(f"[{self.meeting_id}] Запуск диаризации...")
            rttm_path = run_diarization(str(combined_audio_filepath), str(self.output_dir))
            
            # Обработка RTTM и транскрипция (возможно, слияние с результатами онлайн STT)
            logger.info(f"[{self.meeting_id}] Обработка диаризации и транскрипция...")
            dialogue_transcript = process_rttm_and_transcribe(rttm_path, str(combined_audio_filepath))
            print(f"Это вывод диалога: \n{dialogue_transcript}")

            # Убираем метки спикеров, что икслючить засорение промптов
            import re
            pattern = r"\[speaker_\d+\]:\s*"
            cleaned_dialogue = re.sub(pattern, "", dialogue_transcript)

            # Суммаризация
            logger.info(f"[{self.meeting_id}] Создание резюме...")
            summary_text = get_summary_response(cleaned_dialogue)
            print(f"Это вывод summary: \n{summary_text}")
            
            # Генерация заголовка
            logger.info(f"[{self.meeting_id}] Создание заголовка...")
            title_text = get_title_response(cleaned_dialogue)
            print(f"Это вывод заголовка: \n{title_text}")
            
            # Отправка результатов на внешний сервер
            self._send_results_to_backend(dialogue_transcript, summary_text, title_text)
            
            # Сохранение резюме
            # summary_filename = f"summary_{self.meeting_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            # summary_filepath = self.summary_output_dir / summary_filename

        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Ошибка при постобработке: {e}", exc_info=True)
        finally:
            logger.info(f"[{self.meeting_id}] Постобработка завершена.")

    # Функция отправки результатов на внешний сервер
    def _send_results_to_backend(self, full_text: str, summary: str, title: str):
        try:
            meeting_id_int = int(self.meeting_id) if isinstance(self.meeting_id, str) else self.meeting_id
            
            payload = {
                "meeting_id": meeting_id_int,
                "full_text": full_text,
                "summary": summary,
                "title": title
            }
            headers = {
                "X-Internal-Api-Key": "key",
                "Content-Type": "application/json"
            }
            backend_url = os.getenv('MAIN_BACKEND_URL', 'https://maryrose.by')
            url = f"{backend_url}/meetings/internal/result"
            
            logger.info(f"[{self.meeting_id}] Отправляю результаты на backend...")
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=30
            )
            
            response.raise_for_status()
            logger.info(f"[{self.meeting_id}] ✅ Результаты успешно отправлены на backend")
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Ошибка при отправке результатов на backend: {e}")
            logger.error(f"[{self.meeting_id}] ❌ Ошибка при отправке результатов: {e}")
        except ValueError as e:
            print(f"❌ Ошибка преобразования meeting_id в число: {e}")
            logger.error(f"[{self.meeting_id}] ❌ Ошибка meeting_id: {e}")
        except Exception as e:
            print(f"❌ Неожиданная ошибка при отправке результатов: {e}")
            logger.error(f"[{self.meeting_id}] ❌ Неожиданная ошибка: {e}")

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
            logger.infog(f"❌ Ошибка при сохранении аудиофрагмента: {e}")

    # Запуск работы бота
    def run(self):
        """Основной метод, выполняющий всю работу."""
        logger.info(f"[{self.meeting_id}] Бот запускается...")
        try:
            self._initialize_driver()
            
            # Попытка присоединиться к встрече
            joined_successfully = self.join_meet_as_guest()
            
            if joined_successfully:
                logger.info(f"[{self.meeting_id}] Успешно вошел в конференцию, запускаю основные процессы.")
                device_id = self._find_device_id()

                processor_thread = threading.Thread(target=self._process_audio_stream)
                processor_thread.start()
                
                monitor_thread = threading.Thread(target=self._monitor_participants)
                monitor_thread.daemon = True
                monitor_thread.start()

                logger.info(f"[{self.meeting_id}] 🎤 Начинаю прослушивание аудио с устройства ID {device_id}...")
                with sd.RawInputStream(
                    samplerate=STREAM_SAMPLE_RATE,
                    blocksize=self.frame_size,
                    device=device_id,
                    dtype='int16',
                    channels=1,
                    callback=self._audio_capture_callback
                ):
                    processor_thread.join()
                
                logger.info(f"[{self.meeting_id}] Поток прослушивания остановлен.")
            else:
                logger.warning(f"[{self.meeting_id}] Не удалось присоединиться к встрече. Завершаю работу.")

        except Exception as e:
            logger.critical(f"[{self.meeting_id}] ❌ Критическая ошибка в работе бота: {e}", exc_info=True)
        finally:
            self.stop()
            logger.info(f"[{self.meeting_id}] Бот полностью остановлен.")

    # Остановка бота
    def stop(self):
        if not self.is_running.is_set():
            return
        
        logger.info(f"[{self.meeting_id}] Получена команда на завершение...")

        self.is_running.clear()
        
        if self.joined_successfully:
            post_processing_thread = threading.Thread(target=self._perform_post_processing)
            post_processing_thread.daemon = False
            post_processing_thread.start()
        else:
            logger.info(f"[{self.meeting_id}] Пропускаю постобработку, так как вход в конференцию не был успешно завершен.")

        if self.driver:
            try:
                logger.info(f"[{self.meeting_id}] Закрытие WebDriver...")
                self.driver.quit()
            except Exception as e:
                logger.error(f"[{self.meeting_id}] Ошибка при закрытии WebDriver: {e}")
        
        logger.info(f"[{self.meeting_id}] Сессия завершена.")