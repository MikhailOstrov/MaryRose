# api/meet_bot.py
import time
import subprocess
import threading
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import os

from config import MEETINGS_DIR, CHROME_PROFILE_DIR
from api import diarization_handler, ollama_handler

class MeetBot:
    def __init__(self, meet_url: str, meeting_id: str):
        self.meet_url = meet_url
        self.meeting_id = meeting_id
        self.is_running = False
        self.driver = None
        self.vdisplay_proc = None
        self.ffmpeg_proc = None
        self.thread = None
        self.save_path = MEETINGS_DIR / f"{self.meeting_id}.wav"

    def start(self):
        self.thread = threading.Thread(target=self._run)
        self.thread.daemon = True
        self.thread.start()

    def _run(self):
        self.is_running = True
        try:
            self._start_virtual_devices()
            self._join_meet()
            self._record_stream()
        except Exception as e:
            print(f"Bot for {self.meeting_id} failed: {e}")
        finally:
            self.stop()

    def _start_virtual_devices(self):
        print("Starting virtual devices...")
        os.environ['DISPLAY'] = ':1'
        self.vdisplay_proc = subprocess.Popen(["Xvfb", ":1", "-screen", "0", "1280x720x24", "-ac"])
        time.sleep(2)
        # Настройка PulseAudio для перехвата звука
        subprocess.run("pactl load-module module-null-sink sink_name=meet_sink sink_properties=device.description=MeetSink", shell=True)
        subprocess.run("pactl load-module module-remap-source master=meet_sink.monitor source_name=meet_mic source_properties=device.description=MeetMic", shell=True)
        os.environ['PULSE_SINK'] = 'meet_sink'

    def _join_meet(self):
        print("Joining meet with Selenium...")
        chrome_options = Options()
        chrome_options.add_argument(f"--user-data-dir={CHROME_PROFILE_DIR}")
        chrome_options.add_argument("--profile-directory=Default")
        chrome_options.add_argument("--autoplay-policy=no-user-gesture-required")
        chrome_options.add_argument("--no-sandbox")
        service = Service(executable_path="/usr/local/bin/chromedriver")
        self.driver = webdriver.Chrome(service=service, options=chrome_options)
        self.driver.get(self.meet_url)

        # Отключаем микрофон и камеру на странице входа
        time.sleep(5)
        self.driver.find_element(By.XPATH, '//*[@id="yDmH0d"]/c-wiz/div/div/div[28]/div[3]/div/div[2]/div[4]/div[2]/div/div[1]/div[1]/div/div[4]/div[1]/div/div/div').click()
        self.driver.find_element(By.XPATH, '//*[@id="yDmH0d"]/c-wiz/div/div/div[28]/div[3]/div/div[2]/div[4]/div[2]/div/div[1]/div[1]/div/div[4]/div[2]/div/div').click()
        
        # Нажимаем "Присоединиться"
        join_button = WebDriverWait(self.driver, 20).until(EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Присоединиться')]")))
        join_button.click()
        print("Successfully joined the meet.")

    def _record_stream(self):
        print(f"Recording stream to {self.save_path}...")
        command = ['ffmpeg', '-y', '-f', 'pulse', '-i', 'meet_mic', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', str(self.save_path)]
        self.ffmpeg_proc = subprocess.Popen(command)
        while self.is_running:
            if self.ffmpeg_proc.poll() is not None:
                print("FFmpeg recording process stopped unexpectedly.")
                break
            time.sleep(1)

    def stop(self):
        if not self.is_running: return
        print(f"Stopping bot for {self.meeting_id}...")
        self.is_running = False
        
        if self.ffmpeg_proc: self.ffmpeg_proc.terminate()
        if self.driver: self.driver.quit()
        if self.vdisplay_proc: self.vdisplay_proc.terminate()
        subprocess.run("pactl unload-module module-remap-source", shell=True, check=False)
        subprocess.run("pactl unload-module module-null-sink", shell=True, check=False)
        
        print("Bot stopped. Starting post-analysis...")
        if self.save_path.exists() and self.save_path.stat().st_size > 1024:
            try:
                rttm_path = diarization_handler.run_diarization(str(self.save_path), str(MEETINGS_DIR))
                dialogue = diarization_handler.process_rttm_and_transcribe(rttm_path, str(self.save_path))
                summary = ollama_handler.get_summary_response(dialogue)
                print("--- ANALYSIS COMPLETE ---")
                print("DIALOGUE:\n", dialogue)
                print("\nSUMMARY:\n", summary)
                print("-------------------------")
            except Exception as e:
                print(f"Post-analysis failed: {e}")
        else:
            print("Audio file not found or is empty. Skipping analysis.")
            
        from api.bot_manager import active_bots
        if self.meeting_id in active_bots:
            del active_bots[self.meeting_id]
