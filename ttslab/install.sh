#!/bin/bash
# Установка TTS Lab на Мак (Apple Silicon).
#
#   ./install.sh                      — меню выбора моделей
#   ./install.sh all                  — все модели
#   ./install.sh silero-v5 omnivoice  — только перечисленные
#   ./install.sh --no-download ...    — только окружения, веса скачаются при первом запуске
#
# Каждая группа моделей ставится в своё окружение venvs/<группа>, чтобы их
# зависимости (разные версии torch/transformers) не конфликтовали.
set -u
cd "$(dirname "$0")"
ROOT="$(pwd)"
mkdir -p logs data refs outputs models venvs
LOG="$ROOT/logs/install.log"

say()  { printf "\n\033[1m%s\033[0m\n" "$*"; echo "[$(date '+%F %T')] $*" >> "$LOG"; }
warn() { printf "\033[33m%s\033[0m\n" "$*"; echo "[$(date '+%F %T')] WARN $*" >> "$LOG"; }
fail() { printf "\033[31m%s\033[0m\n" "$*"; echo "[$(date '+%F %T')] ERROR $*" >> "$LOG"; }
run()  { echo "+ $*" >> "$LOG"; "$@" 2>&1 | tee -a "$LOG"; return "${PIPESTATUS[0]}"; }

DOWNLOAD=1
ARGS=""
for a in "$@"; do
  case "$a" in
    --no-download) DOWNLOAD=0 ;;
    -h|--help) sed -n 2,11p "$0"; exit 0 ;;
    *) ARGS="$ARGS $a" ;;
  esac
done

say "TTS Lab: установка ($(date '+%F %T'))"

# ---------- система
if [ "$(uname -s)" != "Darwin" ]; then warn "Это не macOS. MLX-модели (Qwen3, VoxCPM2, OmniVoice, Fish, Higgs) работать не будут."; fi
if [ "$(uname -m)" != "arm64" ]; then warn "Это не Apple Silicon. MLX-модели работать не будут."; fi

if ! command -v uv >/dev/null 2>&1; then
  say "Ставлю uv (менеджер Python-окружений)"
  if command -v brew >/dev/null 2>&1; then run brew install uv; else curl -LsSf https://astral.sh/uv/install.sh | sh; fi
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || { fail "uv не установился. Поставьте вручную: brew install uv"; exit 1; }

if ! command -v ffmpeg >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    say "Ставлю ffmpeg (mp3-ответы и загрузка образцов в любом формате)"
    run brew install ffmpeg || warn "ffmpeg не установился — ответы будут в wav, это не мешает работе"
  else
    warn "Нет ffmpeg и Homebrew: ответы будут только в wav. Это не мешает LiteLLM."
  fi
fi

[ -f config.env ] || cp config.example.env config.env

# ---------- окружения
make_env() {  # $1 = группа, $2 = версия python
  local g="$1" py="$2"
  if [ -x "venvs/$g/bin/python" ] && [ -f "venvs/$g/.ok" ] && [ "venvs/$g/.ok" -nt "requirements/$g.txt" ]; then
    echo "  окружение $g уже готово"; return 0
  fi
  say "Окружение «$g» (Python $py)"
  run uv venv --python "$py" "venvs/$g" || return 1
  run uv pip install --python "venvs/$g/bin/python" -r "requirements/$g.txt" || return 1
  touch "venvs/$g/.ok"
}

make_env core 3.12 || { fail "Не удалось создать окружение панели"; exit 1; }
CORE="venvs/core/bin/python"

# список моделей: id|группа|python|название|размер
MODELS_TXT="$($CORE -c '
from server.registry import MODELS, GROUPS
for m in sorted(MODELS, key=lambda m: m["rank"]):
    print("|".join([m["id"], m["group"], GROUPS[m["group"]]["python"], m["title"], m["size"]]))
')"

if [ -z "$(echo $ARGS)" ]; then
  echo
  echo "Какие модели поставить? (Silero ставится всегда: она создаёт голоса-образцы)"
  i=0
  echo "$MODELS_TXT" | while IFS='|' read -r id g py title size; do
    i=$((i+1)); printf "  %2d) %-30s %-14s %s\n" "$i" "$title" "[$g]" "$size"
  done
  echo "   a) все"
  printf "Номера через пробел [a]: "
  read -r CHOICE
  CHOICE="${CHOICE:-a}"
  if [ "$CHOICE" = "a" ] || [ "$CHOICE" = "all" ]; then
    ARGS="all"
  else
    ARGS=""
    for n in $CHOICE; do
      id="$(echo "$MODELS_TXT" | sed -n "${n}p" | cut -d'|' -f1)"
      [ -n "$id" ] && ARGS="$ARGS $id"
    done
  fi
fi

if [ "$(echo $ARGS)" = "all" ]; then
  SELECTED="$(echo "$MODELS_TXT" | cut -d'|' -f1 | tr '\n' ' ')"
else
  SELECTED="$ARGS"
fi
case " $SELECTED " in *" silero-v5 "*) ;; *) SELECTED="silero-v5 $SELECTED" ;; esac
say "Модели: $SELECTED"

# группы, нужные выбранным моделям
GROUPS_NEEDED=""
for id in $SELECTED; do
  line="$(echo "$MODELS_TXT" | grep "^$id|")"
  [ -z "$line" ] && { warn "Нет модели $id — пропускаю"; continue; }
  g="$(echo "$line" | cut -d'|' -f2)"; py="$(echo "$line" | cut -d'|' -f3)"
  case " $GROUPS_NEEDED " in *" $g:"*) ;; *) GROUPS_NEEDED="$GROUPS_NEEDED $g:$py" ;; esac
done

FAILED_GROUPS=""
for gp in $GROUPS_NEEDED; do
  g="${gp%%:*}"; py="${gp##*:}"
  make_env "$g" "$py" || { fail "Окружение $g не установилось — подробности в logs/install.log"; FAILED_GROUPS="$FAILED_GROUPS $g"; }
done

# ---------- веса
OK=""; BAD=""
if [ "$DOWNLOAD" = "1" ]; then
  for id in $SELECTED; do
    line="$(echo "$MODELS_TXT" | grep "^$id|")"; [ -z "$line" ] && continue
    g="$(echo "$line" | cut -d'|' -f2)"
    case " $FAILED_GROUPS " in *" $g "*) BAD="$BAD $id"; continue ;; esac
    say "Скачиваю веса: $(echo "$line" | cut -d'|' -f4) ($(echo "$line" | cut -d'|' -f5))"
    if run "venvs/$g/bin/python" -m server.worker --model "$id" --download-only; then OK="$OK $id"; else BAD="$BAD $id"; fail "Не скачалась $id"; fi
    if [ "$id" = "silero-v5" ]; then
      say "Создаю голоса-образцы refs/default_female и default_male"
      run venvs/silero/bin/python -m server.make_refs || warn "Образцы не создались"
    fi
  done
fi

chmod +x start.sh stop.sh status.sh service.sh 2>/dev/null
PORT="$(grep '^PANEL_PORT=' config.env | cut -d= -f2)"; PORT="${PORT:-8800}"
say "Готово."
[ -n "$OK" ]  && echo "  Установлены:$OK"
[ -n "$BAD" ] && echo "  С ошибками:$BAD  (см. logs/install.log; повторить: ./install.sh$BAD)"
cat <<EOF

Дальше:
  ./start.sh                 — запустить панель
  http://localhost:$PORT/admin  — выбрать и запустить модель
  http://localhost:$PORT/       — тестировать, слушать, оценивать
  ./service.sh install       — автозапуск панели при входе в систему (по желанию)
EOF
