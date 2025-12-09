import subprocess
import logging

logger = logging.getLogger(__name__)

def run_pa_command(command: list[str]) -> tuple[bool, str, str]:
    """Вспомогательная функция для выполнения команд pactl."""
    try:
        process = subprocess.run(command, capture_output=True, text=True, check=True, timeout=5)
        return True, process.stdout.strip(), ""
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip()
        logger.error(f"Ошибка выполнения команды PulseAudio '{' '.join(command)}': {stderr}")
        return False, "", stderr
    except subprocess.TimeoutExpired:
        logger.error(f"Тайм-аут выполнения команды PulseAudio: '{' '.join(command)}'")
        return False, "", "Timeout"
    except FileNotFoundError:
        logger.error("Команда 'pactl' не найдена. Убедитесь, что PulseAudio установлен и доступен в PATH.")
        return False, "", "pactl not found"

class VirtualAudioManager:
    def __init__(self, meeting_id: str):
        self.meeting_id = meeting_id
        # Генерируем уникальные имена
        self.sink_name = f"meet_sink_{self.meeting_id}"
        self.source_name = f"meet_mic_{self.meeting_id}" # Это будет наш виртуальный микрофон для Meet
        self.monitor_name = f"{self.sink_name}.monitor" # Это то, что слушает бот
        
        self.sink_module_id = None
        self.remap_source_module_id = None # Используем remap-source вместо loopback
        self.created_successfully = False

    def create_devices(self):
        """Создает виртуальный sink и remap-source (виртуальный микрофон), который его слушает."""
        logger.info(f"[{self.meeting_id}] Создание виртуальных аудиоустройств: {self.sink_name}, {self.source_name}")

        # 1. Создаем Null Sink (виртуальные колонки). Звук от Chrome пойдет сюда.
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
        logger.info(f"[{self.meeting_id}] Null Sink '{self.sink_name}' создан (module-id: {self.sink_module_id})")

        # 2. Создаем Remap Source. Это и будет наш виртуальный микрофон для Meet.
        # Он берет звук из "монитора" нашего sink'а и представляет его как обычный микрофон.
        cmd_remap = [
            "pactl", "load-module", "module-remap-source",
            f"source_name={self.source_name}",
            f"master={self.monitor_name}",
            f"source_properties=device.description='Virtual_Mic_for_Meet_{self.meeting_id}'"
        ]
        success, output, _ = run_pa_command(cmd_remap)
        if not success:
            logger.error(f"[{self.meeting_id}] Не удалось создать remap-source.")
            self.destroy_devices() # Откатываем изменения
            return False
        self.remap_source_module_id = output
        logger.info(f"[{self.meeting_id}] Remap Source '{self.source_name}' создан (module-id: {self.remap_source_module_id})")
        
        self.created_successfully = True
        logger.info(f"[{self.meeting_id}] ✅ Виртуальные аудиоустройства успешно созданы.")
        return True

    def destroy_devices(self):
        """Удаляет созданные модули PulseAudio в обратном порядке."""
        logger.info(f"[{self.meeting_id}] Уничтожение виртуальных аудиоустройств.")
        # Сначала удаляем remap-source, который зависит от sink'а
        if self.remap_source_module_id:
            run_pa_command(["pactl", "unload-module", self.remap_source_module_id])
            self.remap_source_module_id = None
        # Затем удаляем сам sink
        if self.sink_module_id:
            run_pa_command(["pactl", "unload-module", self.sink_module_id])
            self.sink_module_id = None
        logger.info(f"[{self.meeting_id}] Виртуальные аудиоустройства уничтожены.")