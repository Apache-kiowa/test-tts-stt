#!/bin/bash
# Запуск панели TTS Lab в фоне.
#   ./start.sh                  — панель (+ модель, работавшая в прошлый раз, если AUTOSTART_LAST=1)
#   ./start.sh --model omnivoice — панель и сразу эта модель
#   ./start.sh --fg             — панель в этом окне (видно логи, Ctrl+C — стоп)
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

if [ -f data/panel.pid ] && kill -0 "$(cat data/panel.pid)" 2>/dev/null; then
  echo "Панель уже работает (pid $(cat data/panel.pid))"
else
  if lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Порт $PORT занят: $(lsof -tiTCP:"$PORT" -sTCP:LISTEN | tr '\n' ' ') — смените PANEL_PORT в config.env или остановите процесс"; exit 1
  fi
  if [ "$FG" = "1" ]; then exec "$PY" -m server.controller; fi
  CAF=""; [ "$(get USE_CAFFEINATE)" != "0" ] && command -v caffeinate >/dev/null && CAF="caffeinate -i"
  nohup $CAF "$PY" -m server.controller >> logs/panel.out 2>&1 &
  echo $! > data/panel.pid
  for i in $(seq 1 30); do curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break; sleep 0.5; done
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 || { echo "Панель не поднялась, смотрите logs/panel.out"; tail -20 logs/panel.out; exit 1; }
  echo "Панель запущена (pid $(cat data/panel.pid))"
fi

AUTH=""; PW="$(get PANEL_PASSWORD)"; [ -n "$PW" ] && AUTH="-u admin:$PW"
if [ -z "$MODEL" ] && [ "$(get AUTOSTART_LAST)" != "0" ] && [ -f data/state.json ]; then
  MODEL="$("$PY" -c 'import json;s=json.load(open("data/state.json"));print(s.get("model","") if not s.get("stopped") else "")' 2>/dev/null)"
  RUNNING="$(curl -sf $AUTH "http://127.0.0.1:$PORT/api/status" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["state"].get("running",False))' 2>/dev/null)"
  [ "$RUNNING" = "True" ] && MODEL=""
fi
if [ -n "$MODEL" ]; then
  LOAD="$("$PY" -c 'import json,sys;s=json.load(open("data/state.json")) if __import__("os").path.exists("data/state.json") else {};print(json.dumps(s.get("load_options",{}) if s.get("model")==sys.argv[1] else {}))' "$MODEL")"
  echo "Запускаю модель $MODEL"
  curl -sf $AUTH -X POST "http://127.0.0.1:$PORT/api/start" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$MODEL\",\"load_options\":$LOAD}" >/dev/null || echo "Не удалось запустить $MODEL — откройте /admin"
fi

IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo localhost)"
echo
echo "  Тест:       http://$IP:$PORT/"
echo "  Управление: http://$IP:$PORT/admin"
