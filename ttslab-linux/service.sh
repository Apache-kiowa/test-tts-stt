#!/bin/bash
# Автозапуск панели через systemd (пользовательский сервис), чтобы озвучка поднималась после перезагрузки.
#   ./service.sh install | uninstall | status | logs
cd "$(dirname "$0")"
ROOT="$(pwd)"
NAME="ttslab"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/$NAME.service"

case "$1" in
  install)
    mkdir -p "$UNIT_DIR" logs
    cat > "$UNIT" <<EOF
[Unit]
Description=TTS Lab (панель и модель озвучки)
After=network-online.target

[Service]
WorkingDirectory=$ROOT
ExecStart=$ROOT/start.sh --fg
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
# модель — дочерний процесс панели; при остановке сервиса останавливается и она
KillMode=control-group
TimeoutStopSec=20

[Install]
WantedBy=default.target
EOF
    ./stop.sh >/dev/null 2>&1
    systemctl --user daemon-reload
    systemctl --user enable --now "$NAME" && echo "Сервис $NAME запущен и включён в автозапуск"
    if ! loginctl show-user "$USER" 2>/dev/null | grep -q "Linger=yes"; then
      echo "Чтобы сервис работал без входа в систему, выполните один раз:"
      echo "  sudo loginctl enable-linger $USER"
    fi
    ;;
  uninstall)
    systemctl --user disable --now "$NAME" 2>/dev/null; rm -f "$UNIT"; systemctl --user daemon-reload; echo "Сервис удалён" ;;
  status)
    systemctl --user status "$NAME" --no-pager ;;
  logs)
    journalctl --user -u "$NAME" -n 100 --no-pager ;;
  *) echo "Использование: ./service.sh install | uninstall | status | logs" ;;
esac
