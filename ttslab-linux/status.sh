#!/bin/bash
# Короткая сводка: панель, модель, видеокарта.
cd "$(dirname "$0")"
get() { grep "^$1=" config.env 2>/dev/null | tail -1 | cut -d= -f2-; }
PP="$(get PANEL_PORT)"; TP="$(get TTS_PORT)"; KEY="$(get TTS_API_KEY)"
echo "Панель :${PP:-8800}  $(curl -sf "http://127.0.0.1:${PP:-8800}/health" >/dev/null && echo работает || echo не отвечает)"
echo "Модель :${TP:-8890}  $(curl -sf "http://127.0.0.1:${TP:-8890}/health" || echo не отвечает)"
command -v nvidia-smi >/dev/null && { echo; nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv; }
AUTH=""; [ -n "$KEY" ] && AUTH="-H 'Authorization: Bearer $KEY' "
echo
echo "Проверка синтеза:"
echo "  curl http://127.0.0.1:${TP:-8890}/v1/audio/speech ${AUTH}-H 'Content-Type: application/json' -d '{\"input\":\"Запись на 17:00\",\"voice\":\"alloy\",\"response_format\":\"wav\"}' -o test.wav"
echo
echo "Последние строки логов модели:"; tail -5 logs/worker.log 2>/dev/null
