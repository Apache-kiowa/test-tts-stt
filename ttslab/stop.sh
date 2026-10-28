#!/bin/bash
# Остановить модель и панель.  ./stop.sh model — только модель.
cd "$(dirname "$0")"
PY="venvs/core/bin/python"; [ -x "$PY" ] || PY=python3
WPID="$("$PY" -c 'import json;print(json.load(open("data/state.json")).get("pid",""))' 2>/dev/null)"
if [ -n "$WPID" ] && kill -0 "$WPID" 2>/dev/null; then
  PGID="$(ps -o pgid= -p "$WPID" | tr -d ' ')"
  kill -TERM -- "-$PGID" 2>/dev/null || kill "$WPID"
  sleep 2; kill -0 "$WPID" 2>/dev/null && kill -KILL -- "-$PGID" 2>/dev/null
  echo "Модель остановлена (pid $WPID)"
fi
[ "$1" = "model" ] && exit 0
if [ -f data/panel.pid ] && kill -0 "$(cat data/panel.pid)" 2>/dev/null; then
  kill "$(cat data/panel.pid)"; echo "Панель остановлена"
fi
rm -f data/panel.pid
