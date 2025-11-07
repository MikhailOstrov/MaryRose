#!/bin/bash
set -e

echo "=== [Entrypoint] Запуск от пользователя: $(whoami) ==="

# --- 1. ЗАПУСК SSH-СЕРВЕРА (от root) ---
# Этот блок выполняется, только если скрипт запущен от root (UID 0)
if [ "$(id -u)" = "0" ]; then
    echo "[Entrypoint] Запускаем SSH-сервер в фоновом режиме..."
    /usr/sbin/sshd
    echo "✅ [Entrypoint] SSH-сервер запущен."
else
    echo "[Entrypoint] ВНИМАНИЕ: Скрипт запущен не от root, SSH-сервер не будет запущен."
fi

# --- 2. Настройка /workspace (от root) ---
echo "[Entrypoint] Проверка и настройка /workspace..."
mkdir -p /workspace/.cache/torch /workspace/.cache/nemo /workspace/.cache/huggingface /workspace/models /workspace/logs
export TORCH_HOME=/workspace/.cache/torch
export HF_HOME=/workspace/.cache/huggingface
export LOGS_DIR=/workspace/logs
echo "✅ [Entrypoint] /workspace настроен."

# --- 3. ЗАПУСК PULSEAUDIO В СИСТЕМНОМ РЕЖИМЕ (от root) ---
echo "[Entrypoint] Запуск PulseAudio в системном режиме..."
pulseaudio --system --disallow-exit --exit-idle-time=-1
sleep 2 # Пауза для инициализации

# Проверяем, что appuser может подключиться к системному демону
echo "[Entrypoint] Проверка служб (подключение от appuser)..."
if ! gosu appuser pactl info >/dev/null 2>&1; then
    echo "❌ [Entrypoint] CRITICAL: appuser не может подключиться к системному демону PulseAudio."
    exit 1
fi
echo "✅ [Entrypoint] PulseAudio готов и доступен для appuser."

# --- 4. Предзагрузка моделей (может выполняться от root, если нужно) ---
# ... (ваш код предзагрузки) ...

# --- 5. Финальная диагностика ---
echo "=== [Entrypoint] Проверка системы ==="
echo "Chrome version: $(google-chrome --version 2>/dev/null || echo 'Chrome не найден')"
# ... (остальной ваш код диагностики) ...

# --- 6. Запуск основного приложения (от appuser) ---
echo "=== [Entrypoint] Запуск основного приложения... ==="
exec gosu appuser "$@"