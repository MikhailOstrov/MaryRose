#!/bin/bash
set -e

echo "=== [Entrypoint] Проверка системных ресурсов ==="
df -h
echo "=============================================="

echo "=== [Entrypoint] Запуск от пользователя: $(whoami) ==="

# --- 0. Настройка /workspace ---
echo "[Entrypoint] Проверка и настройка /workspace..."
mkdir -p /workspace/.cache/torch /workspace/.cache/nemo /workspace/.cache/huggingface /workspace/models /workspace/logs
export TORCH_HOME=/workspace/.cache/torch
export NEMO_CACHE_DIR=/workspace/.cache/nemo
export HF_HOME=/workspace/.cache/huggingface
export LOGS_DIR=/workspace/logs
echo "✅ [Entrypoint] /workspace настроен."



# --- 1. Настройка пользовательского окружения ---
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-"/tmp/runtime-$(whoami)"}
mkdir -p -m 0700 "$XDG_RUNTIME_DIR"

# !!! УДАЛЕНО: Глобальный запуск Xvfb и установка DISPLAY больше не нужны !!!
# Xvfb :99 -screen 0 1280x720x16 -nolisten tcp &
# sleep 2
# export DISPLAY=:99

# Запускаем PulseAudio в пользовательском режиме (это остается)
echo "[Entrypoint] Запуск PulseAudio в пользовательском режиме..."
pulseaudio --start --log-target=stderr --exit-idle-time=-1
sleep 2 # Пауза для инициализации

# --- 2. Проверка служб ---
echo "[Entrypoint] Проверка служб..."
if ! pactl info >/dev/null 2>&1; then
    echo "❌ [Entrypoint] CRITICAL: PulseAudio не запустился."
    exit 1
fi
echo "✅ [Entrypoint] PulseAudio готов."



# --- 4. Предзагрузка моделей (остается без изменений) ---
# ... (ваш код предзагрузки) ...

# --- 5. Финальная диагностика ---
echo "=== [Entrypoint] Проверка системы ==="
echo "Chrome version: $(google-chrome --version 2>/dev/null || echo 'Chrome не найден')"
# ... (остальной ваш код диагностики) ...

# --- 5.5. Запуск Inference Service ---
echo "[Entrypoint] Запуск Inference Service..."
# Создаем лог-файл
mkdir -p /workspace/logs
touch /workspace/logs/inference_service.log

# Запускаем uvicorn в фоне. 
# Важно: запускаем из корня проекта (/app), где лежит MaryRose
cd /app
uvicorn server.inference_service:app --host 0.0.0.0 --port 8001 --log-level info > /workspace/logs/inference_service.log 2>&1 &
INFERENCE_PID=$!

echo "[Entrypoint] Ожидание запуска Inference Service (порт 8001)..."
# Цикл ожидания порта (через curl /health)
# Модель грузится долго, дадим 5 минут (150 * 2s = 300s)
MAX_RETRIES=150 
for ((i=1;i<=MAX_RETRIES;i++)); do
    # Проверяем не просто порт, а статус модели (она вернет 200 только когда загрузится)
    if curl -s http://127.0.0.1:8001/health | grep -q "ok"; then
        echo "✅ [Entrypoint] Inference Service готов и модель загружена (попытка $i)!"
        break
    fi
    
    # Проверка, жив ли процесс
    if ! kill -0 $INFERENCE_PID 2>/dev/null; then
        echo "❌ [Entrypoint] Inference Service упал! Смотри логи /workspace/logs/inference_service.log"
        cat /workspace/logs/inference_service.log
        exit 1
    fi
    
    echo "⏳ [Entrypoint] Ожидание загрузки модели... ($i/$MAX_RETRIES)"
    sleep 2
done

if (( i > MAX_RETRIES )); then
    echo "❌ [Entrypoint] Таймаут ожидания Inference Service."
    kill $INFERENCE_PID
    exit 1
fi

# --- 6. Запуск основного приложения ---
echo "=== [Entrypoint] Запуск основного приложения... ==="
exec "$@"