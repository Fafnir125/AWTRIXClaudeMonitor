#!/bin/bash
# Claude Code hook: marks a session as "thinking" once a turn has run for a while
# (ThinkingDelaySeconds in config.json, default 60). The monitor shows the Claude
# screens on the clock only while such a mark is fresh.
#   UserPromptSubmit -> thinking_hook.sh start
#   Stop, SessionEnd -> thinking_hook.sh stop
# Stop does not fire when a turn is cancelled (Esc), so the timer also watches the
# session transcript: once nothing has been written for IDLE seconds the mark is
# dropped, and after GIVEUP seconds of silence the timer ends itself.
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="$HERE/thinking"
DELAY=$(sed -n 's/.*"ThinkingDelaySeconds" *: *\([0-9][0-9]*\).*/\1/p' "$HERE/config.json" 2>/dev/null | head -1)
DELAY=${DELAY:-60}
IDLE=180
GIVEUP=1800
INPUT=$(cat)
SID=$(printf '%s' "$INPUT" | sed -n 's/.*"session_id" *: *"\([^"]*\)".*/\1/p' | head -1)
SID=${SID:-default}
TRANSCRIPT=$(printf '%s' "$INPUT" | sed -n 's/.*"transcript_path" *: *"\([^"]*\)".*/\1/p' | head -1)
FLAG="$DIR/$SID"
PIDF="$DIR/$SID.pid"
mkdir -p "$DIR"

stop() {
  [ -f "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null
  rm -f "$PIDF" "$FLAG"
}

# Seconds since the transcript was last written; without a transcript, since start.
idle() {
  local last
  last=$(stat -f %m "$TRANSCRIPT" 2>/dev/null) || last=$START
  echo $(( $(date +%s) - last ))
}

case "$1" in
  start)
    stop
    START=$(date +%s)
    [ -f "$TRANSCRIPT" ] || IDLE=$GIVEUP
    # Touched every 30 s so a mark left behind by a crashed session goes stale.
    ( sleep "$DELAY"
      while true; do
        quiet=$(idle)
        [ "$quiet" -ge "$GIVEUP" ] && { rm -f "$FLAG" "$PIDF"; exit 0; }
        if [ "$quiet" -lt "$IDLE" ]; then touch "$FLAG"; else rm -f "$FLAG"; fi
        sleep 30
      done ) >/dev/null 2>&1 &
    echo $! > "$PIDF"; disown ;;
  stop) stop ;;
esac
exit 0
