#!/bin/bash
# Запуск панели TTS Lab в фоне.
#   ./start.sh                   — панель (+ модель, работавшая в прошлый раз, если AUTOSTART_LAST=1)
#   ./start.sh --model omnivoice — панель и сразу эта модель
#   ./start.sh --fg              — панель в этом окне (видно логи, Ctrl+C — стоп; так запускает systemd)
cd "$(dirname "$0")"
mkdir -p logs data
PY="venvs/core/bin/python"
[ -x "$PY" ] || { echo "Сначала ./install.sh"; exit 1; }

get() { grep "^$1=" config.env 2>/dev/null | tail -1 | cut -d= -f2-; }
PORT="$(get PANEL_PORT)"; PORT="${PORT:-8800}"
MODEL=""; FG=0
while [ $# -gt 0 ]; do
  case "$1" in
    --model) MODEL="$2"; shift ;;
    --fg) FG=1 ;;
  esac
  shift
done

if [ "$FG" = "1" ]; then exec "$PY" -m server.controller; fi

if [ -f data/panel.pid ] && kill -0 "$(cat data/panel.pid)" 2>/dev/null; then
  echo "Панель уже работает (pid $(cat data/panel.pid))"
else
  if (command -v ss >/dev/null && ss -ltnH "sport = :$PORT" | grep -q .) ; then
    echo "Порт $PORT занят — смените PANEL_PORT в config.env или остановите процесс:"; ss -ltnpH "sport = :$PORT"; exit 1
  fi
  nohup "$PY" -m server.controller >> logs/panel.out 2>&1 &
  echo $! > data/panel.pid
  for i in $(seq 1 30); do curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break; sleep 0.5; done
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 || { echo "Панель не поднялась, смотрите logs/panel.out"; tail -20 logs/panel.out; exit 1; }
  echo "Панель запущена (pid $(cat data/panel.pid))"
fi

if [ -n "$MODEL" ]; then
  AUTH=""; PW="$(get PANEL_PASSWORD)"; [ -n "$PW" ] && AUTH="-u admin:$PW"
  echo "Запускаю модель $MODEL"
  curl -sf $AUTH -X POST "http://127.0.0.1:$PORT/api/start" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$MODEL\"}" >/dev/null || echo "Не удалось запустить $MODEL — откройте /admin"
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"; IP="${IP:-localhost}"
echo
echo "  Тест:       http://$IP:$PORT/"
echo "  Управление: http://$IP:$PORT/admin"
