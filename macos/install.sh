#!/bin/bash
# Installs AWTRIXClaudeMonitor for the current macOS user.
# Usage: ./install.sh [clock-ip]      (safe to run again, e.g. after moving the folder)
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.awtrixclaudemonitor"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
KEYCHAIN_SERVICE="Claude Code-credentials"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$1"; exit 1; }

echo "AWTRIXClaudeMonitor installer"
echo

# 1. Requirements -----------------------------------------------------------------------
[ "$(uname)" = "Darwin" ] || fail "This port runs on macOS only."
PYTHON="$(command -v python3 || true)"
[ -n "$PYTHON" ] || fail "python3 not found. Install it with: xcode-select --install"
ok "Python: $PYTHON"

CLAUDE_CLI="$(PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH" command -v claude || true)"
if [ -n "$CLAUDE_CLI" ]; then
  ok "Claude Code CLI: $CLAUDE_CLI"
else
  warn "Claude Code CLI not found. It is needed to keep the login fresh."
  warn "Install it from https://claude.com/claude-code and run 'claude' once to log in."
fi

# Only checks that the entry exists; the secret itself is never printed.
if security find-generic-password -s "$KEYCHAIN_SERVICE" >/dev/null 2>&1; then
  ok "Claude login found in the keychain"
else
  warn "No Claude login in the keychain yet. Run 'claude' in Terminal once and log in."
fi

# 2. Clock address ----------------------------------------------------------------------
HOST="$1"
if [ -z "$HOST" ] && [ -f "$HERE/config.json" ]; then
  HOST="$(sed -n 's/.*"AwtrixHost" *: *"\([^"]*\)".*/\1/p' "$HERE/config.json" | head -1)"
fi
if [ -z "$HOST" ]; then
  echo
  echo "  Your clock shows its IP address when it starts up (e.g. 192.168.1.42)."
  read -r -p "  Clock IP or hostname: " HOST
fi
[ -n "$HOST" ] || fail "No clock address given."

if curl -s -m 5 "http://$HOST/api/stats" | grep -q '"version"'; then
  ok "AWTRIX reachable at $HOST"
else
  warn "No AWTRIX answered at $HOST - check the address. Continuing anyway."
fi

# 3. Config -----------------------------------------------------------------------------
if [ -f "$HERE/config.json" ]; then
  "$PYTHON" - "$HERE/config.json" "$HOST" <<'EOF'
import json, sys
path, host = sys.argv[1], sys.argv[2]
cfg = json.load(open(path)); cfg["AwtrixHost"] = host
json.dump(cfg, open(path, "w"), indent=2)
EOF
  ok "Updated config.json"
else
  sed "s/YOUR_CLOCK_IP/$HOST/" "$HERE/config.example.json" > "$HERE/config.json"
  ok "Created config.json"
fi
chmod +x "$HERE/thinking_hook.sh"

# 4. Claude Code hooks ------------------------------------------------------------------
"$PYTHON" "$HERE/setup_hooks.py" add >/dev/null
ok "Claude Code hooks registered in ~/.claude/settings.json (backup: settings.json.bak)"

# 5. Start at login ---------------------------------------------------------------------
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$PYTHON</string>
    <string>$HERE/claude_code_monitor.py</string>
  </array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HERE/monitor.log</string>
  <key>StandardErrorPath</key><string>$HERE/monitor.log</string>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
ok "Monitor runs now and starts automatically at login"

echo
echo "Almost done:"
echo "  • macOS may ask whether Python may find devices on your local network: click Allow."
echo "    (Later: System Settings → Privacy & Security → Local Network → Python)"
echo "  • It may also ask whether 'security' may read the 'Claude Code-credentials' keychain item:"
echo "    click Always Allow."
echo "  • Restart the Claude app (or open a new Claude Code session) so the hooks are picked up."
echo
echo "Try all screens:  python3 \"$HERE/demo.py\""
echo "Log file:         $HERE/monitor.log"
echo "Uninstall:        \"$HERE/uninstall.sh\""
