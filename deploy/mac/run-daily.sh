#!/bin/bash
# Weekday 4 AM Mac run: pull code, fetch + whisper (MLX), deliver the run to the `runs` branch for Cain's 9 AM steps.
# Usage: run-daily.sh [run-name]   (default: today's local date). Safe to rerun; daily/transcribe are resumable.
set -uo pipefail
REPO="${PA_REPO:-$HOME/projects/podcast-alpha}"
cd "$REPO" || exit 1
mkdir -p logs
RUN="${1:-$(date +%Y-%m-%d)}"
LOG="logs/run-$RUN.log"
PA="$REPO/.venv/bin/podcast-alpha"
export PA_WHISPER_BACKEND="${PA_WHISPER_BACKEND:-mlx}"
export PA_PYTHON="${PA_PYTHON:-$REPO/.venv/bin/python}"
exec >>"$LOG" 2>&1
echo "== $(date '+%F %T %Z') run $RUN backend=$PA_WHISPER_BACKEND"

fail() { "$PA" heartbeat --run "$RUN" --state failed --note "$1"; "$PA" deliver --run "$RUN" || true; echo "FAILED: $1"; exit 1; }

# caffeinate keeps the Mac awake until this script exits (transcribe included).
caffeinate -dimsu -w $$ &

git pull --ff-only -q || echo "warn: git pull failed; running current checkout"
"$PA" heartbeat --run "$RUN" --state running --note "fetch"
"$PA" deliver --run "$RUN" || echo "warn: early deliver failed"

OUT=$("$PA" daily --hours 48 --run-name "$RUN" --backend "$PA_WHISPER_BACKEND" 2>&1) || { echo "$OUT"; fail "daily exited non-zero"; }
echo "$OUT"
PID=$(printf '%s\n' "$OUT" | sed -n 's/.*transcribe queued pid=\([0-9]*\).*/\1/p' | tail -1)
if [ -n "$PID" ]; then
  "$PA" heartbeat --run "$RUN" --state running --note "transcribe pid $PID"
  "$PA" deliver --run "$RUN" || true
  i=0
  while kill -0 "$PID" 2>/dev/null; do
    sleep 60; i=$((i + 1))
    if [ $((i % 15)) -eq 0 ]; then  # every 15 min: fresh heartbeat + partial transcripts for the box
      "$PA" heartbeat --run "$RUN" --state running --note "transcribe pid $PID" >/dev/null
      "$PA" deliver --run "$RUN" >/dev/null || true
    fi
  done
fi
"$PA" heartbeat --run "$RUN" --state done --note "transcribe finished"
"$PA" deliver --run "$RUN" || fail "final deliver failed"
echo "== $(date '+%F %T %Z') done"
