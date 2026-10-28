#!/bin/bash
# Установка TTS Lab на Linux (Ubuntu/Debian и похожие).
#
#   ./install.sh                      — меню выбора моделей
#   ./install.sh all                  — все модели
#   ./install.sh silero-v5 omnivoice  — только перечисленные
#   ./install.sh --no-download ...    — только окружения, веса скачаются при первом запуске
#   TORCH_BACKEND=cpu ./install.sh    — принудительно CPU-сборка torch (по умолчанию auto: CUDA, если есть драйвер NVIDIA)
#
# Каждая группа моделей ставится в своё окружение venvs/<группа>, чтобы их
# зависимости (разные версии torch/transformers) не конфликтовали.
set -u
cd "$(dirname "$0")"
ROOT="$(pwd)"
mkdir -p logs data refs outputs models venvs vendor
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
    -h|--help) sed -n 2,13p "$0"; exit 0 ;;
    *) ARGS="$ARGS $a" ;;
  esac
done
export UV_TORCH_BACKEND="${TORCH_BACKEND:-auto}"

say "TTS Lab для Linux: установка ($(date '+%F %T'))"

# ---------- система
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  echo "  Видеокарта: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -1)"
else
  warn "NVIDIA GPU не найдена: модели будут работать на процессоре. Silero — быстро, остальные — медленно."
  [ "$UV_TORCH_BACKEND" = "auto" ] && export UV_TORCH_BACKEND=cpu
fi
echo "  Сборка torch: $UV_TORCH_BACKEND"

NEED=""
command -v ffmpeg >/dev/null 2>&1 || NEED="$NEED ffmpeg"
command -v git >/dev/null 2>&1 || NEED="$NEED git"
command -v curl >/dev/null 2>&1 || NEED="$NEED curl"
command -v sox >/dev/null 2>&1 || NEED="$NEED sox"
command -v lsof >/dev/null 2>&1 || NEED="$NEED lsof"
[ -e /usr/include/portaudio.h ] || NEED="$NEED portaudio19-dev"
ldconfig -p 2>/dev/null | grep -q libsndfile || NEED="$NEED libsndfile1"
if [ -n "$NEED" ]; then
  if command -v apt-get >/dev/null 2>&1; then
    say "Нужны системные пакеты:$NEED"
    SUDO=""; [ "$(id -u)" != "0" ] && SUDO="sudo"
    run $SUDO apt-get update -qq && run $SUDO apt-get install -y -qq $NEED || warn "Не удалось поставить:$NEED — поставьте вручную"
  else
    warn "Поставьте вручную пакеты:$NEED (ffmpeg, git, sox, lsof, portaudio, libsndfile)"
  fi
fi

if ! command -v uv >/dev/null 2>&1; then
  say "Ставлю uv (менеджер Python-окружений)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || { fail "uv не установился: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }

[ -f config.env ] || cp config.example.env config.env

# ---------- окружения
make_env() {  # $1 = группа, $2 = версия python
  local g="$1" py="$2"
  if [ -x "venvs/$g/bin/python" ] && [ -f "venvs/$g/.ok" ] && [ "venvs/$g/.ok" -nt "requirements/$g.txt" ]; then
    echo "  окружение $g уже готово"; return 0
  fi
  say "Окружение «$g» (Python $py)"
  run uv venv --python "$py" "venvs/$g" || return 1
  if [ "$g" = "fish" ]; then
    if [ ! -d vendor/fish-speech/.git ]; then
      run git clone --depth 1 https://github.com/fishaudio/fish-speech vendor/fish-speech || return 1
    fi
    run uv pip install --python "venvs/$g/bin/python" --override requirements/fish-overrides.txt \
      -e vendor/fish-speech -r "requirements/$g.txt" || return 1
  else
    run uv pip install --python "venvs/$g/bin/python" -r "requirements/$g.txt" || return 1
  fi
  touch "venvs/$g/.ok"
}

UV_TORCH_BACKEND=cpu make_env core 3.12 || { fail "Не удалось создать окружение панели"; exit 1; }
CORE="venvs/core/bin/python"

# список моделей: id|группа|python|название|размер|GPU
MODELS_TXT="$($CORE -c '
from server.registry import MODELS, GROUPS
for m in sorted(MODELS, key=lambda m: m["rank"]):
    print("|".join([m["id"], m["group"], GROUPS[m["group"]]["python"], m["title"], m["size"], m.get("gpu", "")]))
')"

if [ -z "$(echo $ARGS)" ]; then
  echo
  echo "Какие модели поставить? (Silero ставится всегда: она создаёт голоса-образцы)"
  i=0
  echo "$MODELS_TXT" | while IFS='|' read -r id g py title size gpu; do
    i=$((i+1)); printf "  %2d) %-30s %-9s GPU: %s\n" "$i" "$title" "$size" "$gpu"
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
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"; IP="${IP:-localhost}"
say "Готово."
[ -n "$OK" ]  && echo "  Установлены:$OK"
[ -n "$BAD" ] && echo "  С ошибками:$BAD  (см. logs/install.log; повторить: ./install.sh$BAD)"
cat <<EOF

Дальше:
  ./start.sh                     — запустить панель
  http://$IP:$PORT/admin   — выбрать и запустить модель
  http://$IP:$PORT/        — тестировать, слушать, оценивать
  ./service.sh install           — автозапуск через systemd (по желанию)
EOF
