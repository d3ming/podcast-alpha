# Mac cutover checklist

The Mac (Apple silicon) runs fetch + MLX whisper at 4:00 on weekdays and pushes the run to the `runs` branch.
The box keeps select, report and Notion publish at 9:00, and only runs daily itself if the Mac didn't deliver.

## Dong's one-time steps (on the Mac)

```bash
# 1. Clone or update
mkdir -p ~/projects && cd ~/projects
[ -d podcast-alpha ] || git clone https://github.com/d3ming/podcast-alpha.git
cd podcast-alpha && git pull --ff-only

# 2. Venv + deps (ffmpeg via Homebrew: brew install ffmpeg)
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e '.[whisper,mac]'
PA_WHISPER_BACKEND=mlx .venv/bin/podcast-alpha doctor   # whisper line should say: backend mlx

# 3. Install the launchd agent (weekdays 04:00, logs in ~/projects/podcast-alpha/logs)
bash deploy/mac/install.sh

# 4. Wake the Mac at 03:55 on weekdays (you run this; it needs your password)
sudo pmset repeat wakeorpoweron MTWRF 03:55:00

# 5. Test run now, then check the branch
launchctl kickstart -k gui/$(id -u)/com.dming.podcast-alpha
tail -f logs/run-$(date +%F).log            # ends with "done"
git ls-remote origin runs                     # the runs branch exists
```

`git push` must work non-interactively from the Mac (the HTTPS credential helper or SSH key you already use).
If the Mac sleeps through 04:00, launchd runs the missed job once on wake. `caffeinate` keeps it awake until transcribe ends.

## What a run does (`run-daily.sh`)

1. `git pull --ff-only`, then a `running` heartbeat (`runs/<date>/heartbeat.json`) is delivered.
2. `podcast-alpha daily --backend mlx`, then a wait on the queued transcribe pid. The heartbeat and partial transcripts are re-delivered every 15 min.
3. A `done` heartbeat (or `failed` with a note), then a final `podcast-alpha deliver`.

`deliver` copies the run folder into a private worktree (`.runs-worktree/`, branch `runs`) and pushes it. It skips
audio, locks, SQLite files and anything named like holdings. main never gets run data.

## Box side (Cain's routines)

- **~4:45 weekdays** (was 4:00): `cd /workspace/podcast-alpha && git pull -q && /workspace/pa-venv/bin/podcast-alpha fallback-check`.
  If the heartbeat is `done`, or `running` and under 90 min old, it pulls the run into `runs/<date>`. If it's missing, `failed` or stale, it runs `daily` on the box (faster-whisper). Either way it exits 0. Use `--dry-run` to see the decision only.
- **9:00**: first `podcast-alpha pull-run --run <date>` (skip this if the 4:45 check fell back), then select, report and publish as before.

Rollback: `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.dming.podcast-alpha.plist` on the Mac, and move the box routine back to plain `daily` at 4:00.
