import subprocess
import logging

logger = logging.getLogger(__name__)

def run_pa_command(command: list[str]) -> tuple[bool, str, str]:
    """Вспомогательная функция для выполнения команд pactl."""
    try:
        process = subprocess.run(command, capture_output=True, text=True, check=True)
        return True, process.stdout.strip(), ""
    except subprocess.CalledProcessError as e:
        logger.error(f"Ошибка выполнения команды PulseAudio '{' '.join(command)}': {e.stderr.strip()}")
        return False, "", e.stderr.strip()
    except FileNotFoundError:
        logger.error("Команда 'pactl' не найдена. Убедитесь, что PulseAudio установлен и доступен в PATH.")
        return False, "", "pactl not found"

class VirtualAudioManager:
    def __init__(self, meeting_id: str):
        self.meeting_id = meeting_id
        # Генерируем уникальные имена для устройств этого бота
        self.sink_name = f"meet_sink_{self.meeting_id}"
        self.source_name = f"meet_mic_{self.meeting_id}"
        self.monitor_name = f"{self.sink_name}.monitor"
        
        self.sink_module_id = None
        self.loopback_module_id = None
        self.created_successfully = False

    def create_devices(self):
        """Создает виртуальный sink и связывает его с source через loopback."""
        logger.info(f"[{self.meeting_id}] Создание виртуальных аудиоустройств: {self.sink_name}, {self.source_name}")

        # 1. Создаем Null Sink (виртуальные колонки)
        # Этот sink будет принимать звук от Chrome
        cmd_sink = [
            "pactl", "load-module", "module-null-sink",
            f"sink_name={self.sink_name}",
            f"sink_properties=device.description='Virtual_Sink_for_Meet_{self.meeting_id}'"
        ]
        success, output, _ = run_pa_command(cmd_sink)
        if not success:
            logger.error(f"[{self.meeting_id}] Не удалось создать null-sink.")
            return False
        self.sink_module_id = output
        logger.info(f"[{self.meeting_id}] Null Sink создан (module-id: {self.sink_module_id})")

        # 2. Создаем Loopback (виртуальный аудио кабель)
        # Он перенаправляет звук из наших TTS-ответов (которые мы будем играть в sink)
        # в виртуальный микрофон, который услышит Google Meet.
        cmd_loopback = [
            "pactl", "load-module", "module-loopback",
            f"source_name={self.source_name}",
            f"sink={self.sink_name}",
            f"source_properties=device.description='Virtual_Mic_for_Meet_{self.meeting_id}'"
        ]
        success, output, _ = run_pa_command(cmd_loopback)
        if not success:
            logger.error(f"[{self.meeting_id}] Не удалось создать loopback-устройство.")
            self.destroy_devices() # Откатываем изменения
            return False
        self.loopback_module_id = output
        logger.info(f"[{self.meeting_id}] Loopback Source создан (module-id: {self.loopback_module_id})")
        
        self.created_successfully = True
        logger.info(f"[{self.meeting_id}] ✅ Виртуальные аудиоустройства успешно созданы.")
        return True

    def destroy_devices(self):
        """Удаляет созданные модули PulseAudio."""
        logger.info(f"[{self.meeting_id}] Уничтожение виртуальных аудиоустройств.")
        if self.loopback_module_id:
            run_pa_command(["pactl", "unload-module", self.loopback_module_id])
            self.loopback_module_id = None
        if self.sink_module_id:
            run_pa_command(["pactl", "unload-module", self.sink_module_id])
            self.sink_module_id = None
        logger.info(f"[{self.meeting_id}] Виртуальные аудиоустройства уничтожены.")