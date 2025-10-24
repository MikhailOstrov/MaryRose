#!/bin/bash
set -e

# === ЧАСТЬ 1: ВЫПОЛНЯЕТСЯ ОТ ПОЛЬЗОВАТЕЛЯ ROOT ===
if [ "$(id -u)" = "0" ]; then
    echo "=== [Entrypoint ROOT] Настройка и запуск системных служб ==="

    # --- 1. Настройка и запуск SSH-сервера ---
    echo "[Entrypoint ROOT] Настройка SSH..."
    mkdir -p /var/run/sshd
    mkdir -p /root/.ssh
    chmod 700 /root/.ssh

    sed -i 's/PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config
    sed -i 's/#PermitRootLogin/PermitRootLogin/' /etc/ssh/sshd_config
    sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config

    if [ -n "$PUBLIC_KEY" ]; then
        echo "[Entrypoint ROOT] Найден публичный SSH ключ. Добавляем его для пользователя root..."
        mkdir -p /root/.ssh
        chmod 700 /root/.ssh
        echo "$PUBLIC_KEY" > /root/.ssh/authorized_keys
        chmod 600 /root/.ssh/authorized_keys
        echo "✅ [Entrypoint ROOT] SSH ключ успешно добавлен."
    else
        echo "⚠️ [Entrypoint ROOT] ВНИМАНИЕ: Переменная \$PUBLIC_KEY не найдена. SSH-доступ по ключу будет невозможен."
    fi

    /usr/sbin/sshd
    echo "✅ [Entrypoint ROOT] SSH-сервер запущен."

    # --- 2. Передача управления пользователю 'appuser' ---
    echo "=== [Entrypoint ROOT] Передача управления пользователю 'appuser'..."
    exec gosu appuser "$0" "$@"
fi  

# === ЧАСТЬ 2: ВЫПОЛНЯЕТСЯ ОТ ПОЛЬЗОВАТЕЛЯ APPUSER ===
echo "=== [Entrypoint APPUSER] Запуск от пользователя: $(whoami) ==="

# --- 0. Настройка /workspace ---
echo "[Entrypoint APPUSER] Проверка и настройка /workspace..."
mkdir -p /workspace/.cache/torch /workspace/.cache/nemo /workspace/.cache/huggingface /workspace/models /workspace/logs
export TORCH_HOME=/workspace/.cache/torch
export NEMO_CACHE_DIR=/workspace/.cache/nemo
export HF_HOME=/workspace/.cache/huggingface
export LOGS_DIR=/workspace/logs
echo "✅ [Entrypoint APPUSER] /workspace настроен."

# --- 1. Настройка пользовательского окружения ---
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-"/tmp/runtime-$(whoami)"}
mkdir -p -m 0700 "$XDG_RUNTIME_DIR"

# --- ИЗМЕНЕННЫЙ БЛОК PULSEAUDIO ---
echo "[Entrypoint APPUSER] Попытка запуска PulseAudio..."
# Пытаемся запустить PulseAudio, но не "падаем", если не получится
if pulseaudio --start --log-target=stderr --exit-idle-time=-1 > /dev/null 2>&1 && pactl info > /dev/null 2>&1; then
    echo "✅ [Entrypoint APPUSER] PulseAudio успешно запущен."
else
    echo "⚠️ [Entrypoint APPUSER] ВНИМАНИЕ: PulseAudio не смог запуститься. Приложение продолжит работу, но функции, связанные со звуком, могут быть недоступны."
fi
# --- КОНЕЦ ИЗМЕНЕННОГО БЛОКА ---

# --- 4. Предзагрузка моделей (остается без изменений) ---
# ... (ваш код предзагрузки) ...

# --- 5. Финальная диагностика ---
echo "=== [Entrypoint APPUSER] Проверка системы ==="
echo "Chrome version: $(google-chrome --version 2>/dev/null || echo 'Chrome не найден')"
# ... (остальной ваш код диагностики) ...

# --- 6. Запуск основного приложения ---
echo "=== [Entrypoint APPUSER] Запуск основного приложения... ==="
exec "$@"