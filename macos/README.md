# AWTRIXClaudeMonitor – macOS port

A macOS port of [ClaudeCodeMonitor](https://github.com/RasmusHochreuter/ClaudeCodeMonitor) by **Rasmus Hochreuter**. The screens, the Clawd character and the usage logic come from his project. This port rewrites them in plain Python and adds one twist:

> **The clock shows your Claude usage only while Claude is busy.** Once a reply has taken longer than a minute, the session and weekly screens appear. When Claude finishes, they disappear and your clock goes back to its normal apps.

Everything else stays as in the original: Clawd walks while you have room, panics when less than 10 % is left, cries when the session is used up, and celebrates with confetti and a melody when a window resets.

| Screen | When |
|---|---|
| Clawd walking, number in cyan | session usage is normal |
| Clawd panicking, number in red | less than 10 % of the session left |
| Clawd crying | session used up |
| `WEEK` with percent | weekly usage, red above 90 % |
| Grey with an orange `!` | no fresh data (login expired, offline …) |
| Confetti and melody | a session or weekly window has reset |

## What you need

- A **Ulanzi TC001** with [AWTRIX3](https://blueforcer.github.io/awtrix3/) firmware, on the same network as your Mac
- **macOS** with `python3`. If it is missing, `xcode-select --install` provides it. No extra packages are needed.
- **[Claude Code](https://claude.com/claude-code)**, the command-line tool, installed and logged in once (see below). You can still do your work in the Claude desktop app.

## Install

```bash
git clone https://github.com/Fafnir125/AWTRIXClaudeMonitor.git
cd AWTRIXClaudeMonitor/macos
./install.sh
```

The installer asks for your clock's IP address. The clock shows it when it starts up. Then it:

1. creates `config.json`
2. adds three hooks to `~/.claude/settings.json`. Your other settings and hooks are kept, and a backup is saved as `settings.json.bak`.
3. registers a login item (`~/Library/LaunchAgents/com.awtrixclaudemonitor.plist`) and starts the monitor

After installing:

- **Allow local network access.** macOS asks whether Python may find devices on your local network. Click *Allow*. If you missed the prompt: *System Settings → Privacy & Security → Local Network → Python*.
- **Allow keychain access.** macOS may ask whether `security` may read the item `Claude Code-credentials`. Click *Always Allow*.
- **Restart the Claude app**, or open a new Claude Code session, so the hooks take effect.

Watch every screen once with:

```bash
python3 demo.py        # 8 seconds per screen; python3 demo.py 3 for a quick run
```

## How the Claude connection works

The usage numbers come from the same endpoint that powers the usage bars in Claude's settings. Reading them costs no tokens.

- **Login:** Claude Code stores its login in the macOS keychain under `Claude Code-credentials`. The monitor reads it from there through the `security` command. It never writes it, logs it or sends it anywhere except `api.anthropic.com`.
- **Keeping it fresh:** The login expires after a few hours. The terminal CLI refreshes it whenever you use it. The **desktop app keeps its own login** and does not update the keychain. So when the stored login has expired, the monitor runs `claude -p "Reply with OK" --model haiku` once, at most every 30 minutes, which makes the CLI refresh it. That is a tiny Haiku request that counts against your plan. Set `"AutoRefreshLogin": false` to switch it off; the clock then shows the last known numbers until you use the CLI yourself.
- **Your numbers are account-wide:** the session and weekly limits cover the desktop app, the CLI and claude.ai together, so the clock shows all of your usage.

## How "only while thinking" works

`thinking_hook.sh` runs as a Claude Code hook:

| Hook | Action |
|---|---|
| `UserPromptSubmit` | starts a timer; after `ThinkingDelaySeconds` it creates a marker file in `thinking/` |
| `Stop`, `SessionEnd` | stops the timer and deletes the marker |

The monitor checks for markers every 2 seconds. It fetches usage every 2 minutes either way, so the numbers are ready when the screens appear. Each marker is refreshed every 30 seconds. If a session crashes, its marker expires after 90 seconds, so a screen never stays stuck on the clock.

## Settings (`config.json`)

| Key | Default | Meaning |
|---|---|---|
| `AwtrixHost` | – | IP or hostname of the clock |
| `PollIntervalMinutes` | `2` | how often usage is fetched |
| `ThinkingDelaySeconds` | `60` | how long a reply must run before the screens appear |
| `AutoRefreshLogin` | `true` | refresh an expired login through the CLI (see above) |
| `ClaudeCli` | `""` | path to `claude`; empty searches the usual places |
| `CelebrationSound` | `true` | play the melody when a window resets |
| `SoundWindows` | always | when the melody may play, see below |
| `WeekPeekIntervalMinutes` / `WeekPeekSeconds` | `10` / `5` | how often and how long the weekly screen is shown in between |

`SoundWindows` holds seven `[start, end]` hour pairs, Monday first. An end above 24 runs past midnight. Example: quiet except Mon–Thu 10–23 and Fri–Sun 11–02:

```json
"SoundWindows": [[10, 23], [10, 23], [10, 23], [10, 23], [11, 26], [11, 26], [11, 26]]
```

Restart the monitor after changing settings:

```bash
launchctl kickstart -k gui/$(id -u)/com.awtrixclaudemonitor
```

## Troubleshooting

All activity goes to `monitor.log` in this folder.

| Log says | Fix |
|---|---|
| `No route to host` | allow local network access for Python (see above) |
| `credentials missing or unreadable` | run `claude` in Terminal once and log in; allow keychain access |
| `rejected the token (401/403)` | the login expired and could not be refreshed: check that `claude` works in Terminal |
| `Rate limited` | wait; the monitor pauses on its own |
| Nothing appears while Claude works | restart the Claude app so the hooks load; check `ls thinking/` during a long reply |

## Uninstall

```bash
./uninstall.sh
```

This removes the login item, the hooks and the Claude screens from the clock. Then you can delete the folder.

## Differences from the original

- Python 3 standard library instead of .NET, and launchd instead of the Windows Task Scheduler
- Login from the macOS keychain instead of `~/.claude/.credentials.json`, with an optional refresh through the CLI
- Screens appear only while Claude is working, through Claude Code hooks
- The clock's auto-rotation stays on, because the Claude screens are only there for a short time
- Optional quiet hours for the melody
- Not ported: the AWTRIX simulator, the screenshot generator and the Windows power scripts

## License

MIT, like the original. See [LICENSE](../LICENSE) (© Rasmus Hochreuter). The macOS port is provided under the same terms.
