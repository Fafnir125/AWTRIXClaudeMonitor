#!/bin/bash
# Removes the login item, the Claude Code hooks and the Claude screens from the clock.
# Leaves this folder (and your config.json) in place.
HERE="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.awtrixclaudemonitor"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Login item removed"

python3 "$HERE/setup_hooks.py" remove

HOST="$(sed -n 's/.*"AwtrixHost" *: *"\([^"]*\)".*/\1/p' "$HERE/config.json" 2>/dev/null | head -1)"
if [ -n "$HOST" ]; then
  for app in claude_session claude_week claude_issue claude_demo; do
    curl -s -m 3 -X POST "http://$HOST/api/custom?name=$app" -d '' >/dev/null
  done
  echo "Claude screens removed from the clock"
fi
rm -rf "$HERE/thinking"
