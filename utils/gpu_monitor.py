# file: utils/gpu_monitor.py

import subprocess
import logging
import os
import requests

logger = logging.getLogger(__name__)

def get_gpu_utilization() -> dict | None:
    """
    Получает текущую утилизацию GPU, выполняя команду nvidia-smi внутри пода.

    Возвращает словарь с метриками или None в случае ошибки.
    Пример возвращаемого значения:
    {
        'utilization_percent': 15, 
        'memory_used_mb': 2048,
        'memory_total_mb': 24576
    }
    """
    try:
        # Команда запрашивает утилизацию GPU, использованную и общую память
        # Формат: csv, без заголовка, без единиц измерения (мы знаем, что это % и МиБ)
        command = [
            "nvidia-smi",
            "--query-gpu=utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader,nounits"
        ]
        
        # Выполняем команду
        result = subprocess.check_output(command, text=True, stderr=subprocess.PIPE)
        
        # Парсим вывод. Пример: "15, 2048, 24576"
        utilization, memory_used, memory_total = result.strip().split(', ')
        
        return {
            'utilization_percent': int(utilization),
            'memory_used_mb': int(memory_used),
            'memory_total_mb': int(memory_total)
        }

    except FileNotFoundError:
        # Это произойдет, если nvidia-smi не установлена или недоступна в PATH
        logger.warning("Команда 'nvidia-smi' не найдена. Невозможно получить метрики GPU.")
        return None
    except subprocess.CalledProcessError as e:
        # Это может случиться, если драйверы NVIDIA не работают
        logger.error(f"Ошибка выполнения nvidia-smi: {e.stderr}")
        return None
    except Exception as e:
        logger.error(f"Неожиданная ошибка при получении метрик GPU: {e}", exc_info=True)
        return None



