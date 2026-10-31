#!/bin/bash
# Claude Code hook: marks a session as "thinking" once a turn has run for a while
# (ThinkingDelaySeconds in config.json, default 60). The monitor shows the Claude
# screens on the clock only while such a mark is fresh.
#   UserPromptSubmit -> thinking_hook.sh start
#   Stop, SessionEnd -> thinking_hook.sh stop
# Stop does not fire when a turn is cancelled (Esc) or killed, so the mark is only
# kept while the session transcript keeps growing: after IDLE seconds without a
# write the mark is dropped, and after GIVEUP seconds the timer ends itself.

# The monitor's own login refresh runs `claude -p`; it must not count as thinking.
[ -n "$AWTRIX_CLAUDE_MONITOR" ] && exit 0

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

# Seconds since the transcript was last written. Without a transcript (yet) the
# session counts as quiet, so it can never keep the mark alive.
idle() {
  [ -f "$TRANSCRIPT" ] || TRANSCRIPT=$(ls "$HOME"/.claude/projects/*/"$SID".jsonl 2>/dev/null | head -1)
  local last
  last=$(stat -f %m "$TRANSCRIPT" 2>/dev/null) || last=0
  echo $(( $(date +%s) - last ))
}

case "$1" in
  start)
    stop
    START=$(date +%s)
    ( sleep "$DELAY"
      while [ $(( $(date +%s) - START )) -lt "$GIVEUP" ]; do
        if [ "$(idle)" -lt "$IDLE" ]; then touch "$FLAG"; else rm -f "$FLAG"; fi
        sleep 30
      done
      rm -f "$FLAG" "$PIDF" ) >/dev/null 2>&1 &
    echo $! > "$PIDF"; disown ;;
  stop) stop ;;
esac
exit 0
