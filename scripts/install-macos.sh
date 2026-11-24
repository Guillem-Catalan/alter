#!/usr/bin/env bash
# Keep Alter running on macOS: two LaunchAgents that start at login and restart if they crash.
#   <label>.bot     python main.py whatsapp
#   <label>.tunnel  ngrok http --url=$NGROK_DOMAIN $PORT   (skipped if NGROK_DOMAIN is empty)
# Usage: scripts/install-macos.sh [--uninstall]
set -euo pipefail
cd "$(dirname "$0")/.."
root=$(pwd)
label=${LABEL:-com.$(id -un).alter}
agents=$HOME/Library/LaunchAgents
logs=$HOME/Library/Logs/Alter

unload() { launchctl bootout "gui/$(id -u)/$1" 2>/dev/null || true; rm -f "$agents/$1.plist"; }

if [[ ${1:-} == --uninstall ]]; then
  unload "$label.bot"; unload "$label.tunnel"
  echo "Removed $label.bot and $label.tunnel"
  exit 0
fi

python=$root/.venv/bin/python
[[ -x $python ]] || { echo "Missing $python. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }
[[ -f .env ]] || { echo "Missing .env. Copy .env.example and fill it in."; exit 1; }
"$python" main.py doctor || { echo "Fix the items above first."; exit 1; }

port=$(grep -E '^(export )?PORT=' .env | tail -1 | cut -d= -f2- | tr -d '"'"'" || true)
domain=$(grep -E '^(export )?NGROK_DOMAIN=' .env | tail -1 | cut -d= -f2- | tr -d '"'"'" || true)
port=${port:-8080}

mkdir -p "$agents" "$logs"

write_agent() { # name program-args...
  local name=$1; shift
  {
    echo '<?xml version="1.0" encoding="UTF-8"?>'
    echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
    echo '<plist version="1.0"><dict>'
    echo "  <key>Label</key><string>$name</string>"
    echo '  <key>ProgramArguments</key><array>'
    for arg in "$@"; do echo "    <string>$arg</string>"; done
    echo '  </array>'
    echo "  <key>WorkingDirectory</key><string>$root</string>"
    echo '  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>'
    echo '  <key>RunAtLoad</key><true/>'
    echo '  <key>KeepAlive</key><true/>'
    echo '  <key>ThrottleInterval</key><integer>10</integer>'
    echo "  <key>StandardOutPath</key><string>$logs/${name##*.}.log</string>"
    echo "  <key>StandardErrorPath</key><string>$logs/${name##*.}.log</string>"
    echo '</dict></plist>'
  } > "$agents/$name.plist"
  launchctl bootout "gui/$(id -u)/$name" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$agents/$name.plist"
  echo "Running: $name"
}

write_agent "$label.bot" "$python" "$root/main.py" whatsapp
if [[ -n $domain ]]; then
  ngrok=$(command -v ngrok) || { echo "ngrok not installed: brew install ngrok/ngrok/ngrok"; exit 1; }
  write_agent "$label.tunnel" "$ngrok" http "--url=$domain" "$port" --log=stdout
  echo "Webhook URL for Meta: https://$domain/webhook"
else
  echo "NGROK_DOMAIN is empty: no tunnel installed. Meta needs a public HTTPS URL pointing to port $port."
fi

echo "Logs: $logs"
echo "To keep it answering, stop the Mac from sleeping while plugged in:"
echo "  sudo pmset -c sleep 0 disksleep 0"
