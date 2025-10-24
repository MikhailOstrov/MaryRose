#!/bin/bash
set -e

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

# --- 6. Запуск основного приложения ---
echo "=== [Entrypoint] Запуск основного приложения... ==="
exec "$@"