#!/bin/bash
# file: monitor.sh

# Лог-файл будет создан в /workspace, чтобы пережить перезапуски контейнера.
LOG_FILE="/workspace/memory_log.txt"

# Добавляем разделитель и метку времени при каждом старте скрипта.
# Это поможет нам увидеть в логе, когда именно контейнер перезапустился.
echo "--- Starting Memory Monitor at $(date) ---" >> $LOG_FILE

# Бесконечный цикл, который пишет данные раз в секунду.
while true; do
  # Получаем использование VRAM в удобном формате: "timestamp, used_memory, total_memory"
  # Это самый важный показатель для ASR-модели.
  VRAM_LOG=$(nvidia-smi --query-gpu=timestamp,memory.used,memory.total --format=csv,noheader)
  
  # Получаем использование RAM (в мегабайтах).
  # Это важно для отслеживания потребления памяти процессами Chrome и Python.
  RAM_LOG=$(free -m | grep Mem | awk '{print "RAM Used: "$3"MB"}')
  
  # Записываем все в одну строку в лог-файл для удобного анализа.
  echo "$(date +%Y-%m-%d_%H:%M:%S) | VRAM: $VRAM_LOG | $RAM_LOG" >> $LOG_FILE
  
  # Ждем 1 секунду перед следующей записью.
  sleep 1
done