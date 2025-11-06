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

# --- 3. ПОДГОТОВКА И ЗАПУСК PULSEAUDIO (от appuser) ---
# Сначала готовим окружение от root
echo "[Entrypoint] Настройка XDG_RUNTIME_DIR для appuser..."
mkdir -p -m 0700 /tmp/runtime-appuser
chown appuser:appuser /tmp/runtime-appuser
echo "✅ [Entrypoint] XDG_RUNTIME_DIR настроен."

# Затем запускаем PulseAudio от appuser с помощью gosu
echo "[Entrypoint] Запуск PulseAudio от имени appuser..."
gosu appuser pulseaudio --start --log-target=stderr --exit-idle-time=-1
sleep 2 # Пауза для инициализации

# Проверяем статус PulseAudio также от имени appuser
echo "[Entrypoint] Проверка служб..."
if ! gosu appuser pactl info >/dev/null 2>&1; then
    echo "❌ [Entrypoint] CRITICAL: PulseAudio не запустился от имени appuser."
    exit 1
fi
echo "✅ [Entrypoint] PulseAudio готов."

# --- 4. Предзагрузка моделей (может выполняться от root, если нужно) ---
# ... (ваш код предзагрузки) ...

# --- 5. Финальная диагностика ---
echo "=== [Entrypoint] Проверка системы ==="
echo "Chrome version: $(google-chrome --version 2>/dev/null || echo 'Chrome не найден')"
# ... (остальной ваш код диагностики) ...

# --- 6. Запуск основного приложения (от appuser) ---
echo "=== [Entrypoint] Запуск основного приложения... ==="
exec gosu appuser "$@"