#!/bin/bash
# Claude Code hook: marks a session as "thinking" once a turn has run for a while
# (ThinkingDelaySeconds in config.json, default 60). The monitor shows the Claude
# screens on the clock only while such a mark is fresh.
#   UserPromptSubmit -> thinking_hook.sh start
#   Stop, SessionEnd -> thinking_hook.sh stop
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="$HERE/thinking"
DELAY=$(sed -n 's/.*"ThinkingDelaySeconds" *: *\([0-9][0-9]*\).*/\1/p' "$HERE/config.json" 2>/dev/null | head -1)
DELAY=${DELAY:-60}
SID=$(cat | sed -n 's/.*"session_id" *: *"\([^"]*\)".*/\1/p' | head -1)
SID=${SID:-default}
FLAG="$DIR/$SID"
PIDF="$DIR/$SID.pid"
mkdir -p "$DIR"

stop() {
  [ -f "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null
  rm -f "$PIDF" "$FLAG"
}

case "$1" in
  start)
    stop
    # Touched every 30 s so a mark left behind by a crashed session goes stale.
    ( sleep "$DELAY"; while true; do touch "$FLAG"; sleep 30; done ) >/dev/null 2>&1 &
    echo $! > "$PIDF"; disown ;;
  stop) stop ;;
esac
exit 0
