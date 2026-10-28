#!/bin/bash
# Автозапуск панели при входе в macOS (launchd), чтобы озвучка не пропадала после перезагрузки.
#   ./service.sh install | uninstall | status
cd "$(dirname "$0")"
ROOT="$(pwd)"
LABEL="ru.ttslab.panel"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

case "$1" in
  install)
    mkdir -p "$HOME/Library/LaunchAgents" logs
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$ROOT/start.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>AbandonProcessGroup</key><true/>
  <key>StandardOutPath</key><string>$ROOT/logs/launchd.out</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/launchd.out</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
</dict></plist>
EOF
    launchctl unload "$PLIST" 2>/dev/null
    launchctl load "$PLIST" && echo "Автозапуск включён: панель и последняя модель стартуют при входе в систему"
    ;;
  uninstall)
    launchctl unload "$PLIST" 2>/dev/null; rm -f "$PLIST"; echo "Автозапуск выключен" ;;
  status)
    launchctl list | grep "$LABEL" || echo "Автозапуск не настроен" ;;
  *) echo "Использование: ./service.sh install | uninstall | status" ;;
esac
