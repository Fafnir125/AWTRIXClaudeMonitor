#!/usr/bin/env python3
"""Claude Code usage on an AWTRIX3 clock - macOS port of RasmusHochreuter/ClaudeCodeMonitor.

Python standard library only. Reads the Claude Code login from the macOS keychain,
polls the usage endpoint and shows a session screen, a weekly screen and Clawd icons
while a Claude turn has been running for a while (see thinking_hook.sh).
"""
import json
import logging
import math
import os
import shutil
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "config.json")

DEFAULTS = {
    "AwtrixHost": "",
    "PollIntervalMinutes": 2,
    "StaleAfterFailures": 3,
    "CelebrationSeconds": 60,
    "CelebrationSound": True,
    "CelebrationMelody": "refill:d=8,o=6,b=200:c,e,g,4c7,p,g,4c7,p,2c7",
    "WeekPeekIntervalMinutes": 10,
    "WeekPeekSeconds": 5,
    "UsageBaseUrl": "https://api.anthropic.com",
    "KeychainService": "Claude Code-credentials",
    # Empty = find `claude` on PATH, ~/.local/bin, /opt/homebrew/bin or /usr/local/bin.
    "ClaudeCli": "",
    # Refresh an expired keychain login with a one-word Haiku prompt via the Claude CLI.
    "AutoRefreshLogin": True,
    # Read by thinking_hook.sh: how long a turn must run before the screens appear.
    "ThinkingDelaySeconds": 60,
    # Seven [start, end] hour pairs, Monday first; end may exceed 24 to run past midnight.
    "SoundWindows": [[8, 22]] * 7,
}

SESSION_APP, WEEK_APP, ISSUE_APP = "claude_session", "claude_week", "claude_issue"

# thinking_hook.sh keeps a file per session here while a turn runs longer than 60 s.
THINKING_DIR = os.path.join(HERE, "thinking")
THINKING_STALE_SECONDS = 90


def thinking_active():
    try:
        names = [n for n in os.listdir(THINKING_DIR) if not n.endswith(".pid")]
    except FileNotFoundError:
        return False
    cutoff = time.time() - THINKING_STALE_SECONDS
    for name in names:
        try:
            if os.path.getmtime(os.path.join(THINKING_DIR, name)) >= cutoff:
                return True
        except FileNotFoundError:
            pass
    return False

log = logging.getLogger("ccm")


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            cfg.update(json.load(f))
    if os.environ.get("AWTRIX_HOST"):
        cfg["AwtrixHost"] = os.environ["AWTRIX_HOST"]
    if not cfg["AwtrixHost"]:
        sys.exit(f"AwtrixHost is missing - run install.sh or set it in {CONFIG_FILE}.")
    return cfg


def now():
    return datetime.now(timezone.utc)


def parse_time(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# --- Credentials (macOS keychain instead of ~/.claude/.credentials.json) ---------------

def read_credentials(service):
    try:
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=10, check=True).stdout
        oauth = json.loads(raw)["claudeAiOauth"]
        token = oauth.get("accessToken")
        if not token:
            return None
        expires = oauth.get("expiresAt")
        expires_at = datetime.fromtimestamp(expires / 1000, timezone.utc) if expires else datetime.max.replace(tzinfo=timezone.utc)
        return token, expires_at
    except (subprocess.SubprocessError, ValueError, KeyError, TypeError) as ex:
        log.warning("Could not read credentials: %s", type(ex).__name__)
        return None


CLI_SEARCH_PATH = "{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def find_claude_cli(configured=""):
    if configured:
        return os.path.expanduser(configured)
    return shutil.which("claude", path=CLI_SEARCH_PATH.format(home=os.path.expanduser("~")))


def refresh_login(configured_cli):
    """Lets the Claude CLI refresh the keychain login. The desktop app keeps its own login
    and never updates the keychain entry, so without this the token goes stale."""
    cli = find_claude_cli(configured_cli)
    if not cli:
        log.warning("Login expired and the Claude CLI was not found - set ClaudeCli in config.json")
        return
    home = os.path.expanduser("~")
    # A clean environment, so a desktop-app session's variables do not redirect the CLI.
    env = {"HOME": home, "USER": os.environ.get("USER", ""), "PATH": CLI_SEARCH_PATH.format(home=home)}
    try:
        subprocess.run([cli, "-p", "Reply with OK", "--model", "haiku"],
                       env=env, cwd="/tmp", capture_output=True, timeout=120)
        log.info("Login refreshed via Claude CLI")
    except (OSError, subprocess.SubprocessError) as ex:
        log.warning("Login refresh failed: %s", ex)


# --- Usage endpoint --------------------------------------------------------------------

def ssl_context():
    """python.org builds ship without CA certs; fall back to the macOS system root store."""
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats()["x509_ca"]:
        return ctx
    pem = subprocess.run(
        ["security", "find-certificate", "-a", "-p", "/System/Library/Keychains/SystemRootCertificates.keychain"],
        capture_output=True, text=True, timeout=30).stdout
    ctx.load_verify_locations(cadata=pem)
    return ctx


SSL_CONTEXT = None


def get_usage(base_url, token):
    global SSL_CONTEXT
    SSL_CONTEXT = SSL_CONTEXT or ssl_context()
    """Returns ("ok", snapshot) | ("unauthorized", None) | ("ratelimited", seconds) | ("transient", reason)."""
    req = urllib.request.Request(base_url.rstrip("/") + "/api/oauth/usage", headers={
        "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"})
    try:
        with urllib.request.urlopen(req, timeout=15, context=SSL_CONTEXT) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as ex:
        if ex.code in (401, 403):
            return "unauthorized", None
        if ex.code == 429:
            retry = ex.headers.get("Retry-After")
            return "ratelimited", int(retry) if retry and retry.isdigit() else None
        return "transient", f"HTTP {ex.code}"
    except (urllib.error.URLError, TimeoutError, ValueError) as ex:
        return "transient", type(ex).__name__

    def window(key):
        w = payload.get(key) or {}
        pct = int(min(max(round(w.get("utilization") or 0), 0), 100))
        return pct, parse_time(w.get("resets_at"))

    s_pct, s_reset = window("five_hour")
    w_pct, w_reset = window("seven_day")
    return "ok", {"session": s_pct, "session_reset": s_reset, "week": w_pct, "week_reset": w_reset}


# --- AWTRIX client ---------------------------------------------------------------------

class Awtrix:
    def __init__(self, host):
        self.base = f"http://{host}/"

    def _request(self, path, body=None, content_type="application/json", timeout=10):
        data = body.encode() if isinstance(body, str) else body
        req = urllib.request.Request(self.base + path, data=data, method="POST" if data is not None else "GET")
        if data is not None:
            req.add_header("Content-Type", content_type)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    def _safe(self, what, fn):
        try:
            fn()
            return True
        except (urllib.error.URLError, TimeoutError, OSError) as ex:
            log.warning("Clock request failed (%s): %s", what, ex)
            return False

    def push_app(self, name, payload):
        body = json.dumps(payload, separators=(",", ":"))
        # The clock intermittently 500s on back-to-back large payloads, so retry once.
        for attempt in (1, 2):
            try:
                self._request(f"api/custom?name={name}", body)
                return True
            except urllib.error.HTTPError as ex:
                if attempt == 2:
                    log.warning("Clock rejected app %s: HTTP %s", name, ex.code)
                    return False
                time.sleep(0.5)
            except (urllib.error.URLError, TimeoutError, OSError) as ex:
                log.warning("Clock unreachable while pushing %s: %s", name, ex)
                return False

    def remove_app(self, name):
        return self._safe(f"remove {name}", lambda: self._request(f"api/custom?name={name}", ""))

    def upload_icon(self, name, gif):
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"/ICONS/{name}.gif\"\r\n"
                f"Content-Type: image/gif\r\n\r\n").encode() + gif + f"\r\n--{boundary}--\r\n".encode()
        return self._safe(f"icon {name}", lambda: self._request("edit", body, f"multipart/form-data; boundary={boundary}"))

    def play_melody(self, rtttl):
        return self._safe("melody", lambda: self._request("api/rtttl", rtttl, "text/plain"))

    def disable_rotation(self):
        return self._safe("settings", lambda: self._request("api/settings", '{"ATRANS":false}'))

    def switch_to(self, name):
        return self._safe(f"switch {name}", lambda: self._request("api/switch", json.dumps({"name": name})))

    def current_app(self):
        try:
            return json.loads(self._request("api/stats")).get("app")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return None


# --- GIF writer + Clawd icons ----------------------------------------------------------

PALETTE = [(0, 0, 0), (0xFF, 0x7A, 0x3D), (0x2E, 0xA8, 0xFF), (0xE9, 0xE9, 0xED),
           (0x1D, 0x6F, 0xA8), (0x59, 0xD0, 0xFF), (0x4A, 0x4F, 0x5E), (0, 0, 0)]
ORANGE_I, EYE_I, WHITE_I, SADEYE_I, TEAR_I, GREY_I = 1, 2, 3, 4, 5, 6


def gif_animated(frames, width, height, delay_cs):
    out = bytearray(b"GIF89a" + struct.pack("<HH", width, height) + bytes([0b11110010, 0, 0]))
    for color in PALETTE:
        out += bytes(color)
    if len(frames) > 1:
        out += b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00"
    for frame in frames:
        out += b"\x21\xF9\x04\x04" + struct.pack("<H", delay_cs) + b"\x00\x00"
        out += b"\x2C" + struct.pack("<HHHH", 0, 0, width, height) + b"\x00\x03"
        out += _encode_pixels(frame)
    out += b"\x3B"
    return bytes(out)


def _encode_pixels(pixels):
    # Uncompressed LZW: a clear code before every pixel keeps the code size at 4 bits.
    data, cur, nbits = bytearray(), 0, 0
    codes = [c for p in pixels for c in (8, p)] + [9]
    for code in codes:
        cur |= code << nbits
        nbits += 4
        while nbits >= 8:
            data.append(cur & 0xFF)
            cur >>= 8
            nbits -= 8
    if nbits:
        data.append(cur & 0xFF)
    blocks = bytearray()
    for i in range(0, len(data), 255):
        chunk = data[i:i + 255]
        blocks += bytes([len(chunk)]) + chunk
    return bytes(blocks + b"\x00")


def _frame(rows, body, eye, tear_row=None, x_offset=0):
    # Full-width 32x8 canvas; +2 puts Clawd's body at columns 3-8.
    pixels = [0] * 256
    for dy, row in enumerate(rows):
        for dx, ch in enumerate(row):
            value = body if ch == "O" else eye if ch in "BW" else 0
            x = dx + x_offset + 2
            if value and 0 <= x < 32:
                pixels[(dy + 1) * 32 + x] = value
    if tear_row is not None:
        pixels[tear_row * 32 + 8] = TEAR_I
    return pixels


def clawd_icons():
    a = [".OOOOOO.", ".OBOOBO.", "OOOOOOOO", ".OOOOOO.", "..O..O.."]
    b = [".OOOOOO.", ".OBOOBO.", "OOOOOOOO", ".OOOOOO.", ".O....O."]
    blink = [".OOOOOO.", ".OOOOOO.", "OOOOOOOO", ".OOOOOO.", "..O..O.."]
    look_a = [".OOOOOO.", ".OOBOOB.", "OOOOOOOO", ".OOOOOO.", "..O..O.."]
    look_b = [".OOOOOO.", ".OOBOOB.", "OOOOOOOO", ".OOOOOO.", ".O....O."]
    pa = [r.replace("B", "W") for r in a]
    pb = [r.replace("B", "W") for r in b]
    animations = {
        "clawd": ([_frame(r, ORANGE_I, EYE_I) for r in (a, b, blink, b, look_a, look_b, a, b)], 80),
        "clawd_panic": ([_frame(pa, ORANGE_I, WHITE_I), _frame(pb, ORANGE_I, WHITE_I, x_offset=-1),
                         _frame(pa, ORANGE_I, WHITE_I, x_offset=-1), _frame(pb, ORANGE_I, WHITE_I)], 30),
        "clawd_sad": ([_frame(a, ORANGE_I, SADEYE_I, tear_row=t) for t in (2, 3, 4)] + [_frame(a, ORANGE_I, SADEYE_I)], 80),
        "clawd_grey": ([_frame(a, GREY_I, GREY_I)], 80),
    }
    return {name: gif_animated(frames, 32, 8, delay) for name, (frames, delay) in animations.items()}


# --- Screen composer -------------------------------------------------------------------

CYAN, ORANGE, PURPLE = "#59d0ff", "#ff7a3d", "#9184d9"
DIM_PURPLE, GREY, DIM_GREY, RED = "#2b2650", "#4a4f5e", "#23262f", "#ff4526"

FONT = {
    "0": ["111", "101", "101", "101", "111"], "1": ["010", "110", "010", "010", "111"],
    "2": ["111", "001", "111", "100", "111"], "3": ["111", "001", "111", "001", "111"],
    "4": ["101", "101", "111", "001", "001"], "5": ["111", "100", "111", "001", "111"],
    "6": ["111", "100", "111", "101", "111"], "7": ["111", "001", "010", "010", "010"],
    "8": ["111", "101", "111", "101", "111"], "9": ["111", "101", "111", "001", "111"],
    ":": ["0", "1", "0", "1", "0"], " ": ["0", "0", "0", "0", "0"],
    "%": ["11001", "11010", "00100", "01011", "10011"],
    "d": ["001", "001", "111", "101", "111"], "h": ["100", "100", "111", "101", "101"],
    "W": ["10001", "10001", "10101", "10101", "01010"], "E": ["111", "100", "111", "100", "111"],
    "K": ["101", "101", "110", "101", "101"], "!": ["1", "1", "1", "0", "1"],
}


class Screens:
    def __init__(self, lifetime):
        self.lifetime = lifetime

    @staticmethod
    def _grid():
        return [[None] * 32 for _ in range(8)]

    @staticmethod
    def _sprite(grid, x, y, rows, palette):
        for dy, row in enumerate(rows):
            for dx, ch in enumerate(row):
                if ch in palette and 0 <= y + dy < 8 and 0 <= x + dx < 32:
                    grid[y + dy][x + dx] = palette[ch]

    def _text(self, grid, x, y, text, color):
        for ch in text:
            glyph = FONT.get(ch, FONT[" "])
            self._sprite(grid, x, y, glyph, {"1": color})
            x += len(glyph[0]) + 1

    @staticmethod
    def _text_width(text):
        return sum(len(FONT.get(ch, FONT[" "])[0]) + 1 for ch in text) - 1

    @staticmethod
    def _segmented_bar(grid, fraction, segments, width, lit=PURPLE, dim=DIM_PURPLE):
        lit_pixels = math.floor(fraction * segments * width + 0.5)
        n = 0
        for segment in range(segments):
            for p in range(width):
                grid[7][1 + segment * (width + 1) + p] = lit if n < lit_pixels else dim
                n += 1

    def _session_bar(self, grid, until, lit=PURPLE, dim=DIM_PURPLE):
        fraction = 1 - min(max(until.total_seconds() / 60 / 300, 0), 1) if until else 0
        self._segmented_bar(grid, fraction, 5, 5, lit, dim)

    @staticmethod
    def _draw_commands(grid):
        # The clock rejects payloads above roughly 2 KB, so same-coloured runs become lines.
        done = [[False] * 32 for _ in range(8)]
        draw = []
        for y in range(8):
            for x in range(32):
                c = grid[y][x]
                if not c or done[y][x]:
                    continue
                x1 = x
                while x1 + 1 < 32 and grid[y][x1 + 1] == c and not done[y][x1 + 1]:
                    x1 += 1
                y1 = y
                if x1 == x:
                    while y1 + 1 < 8 and grid[y1 + 1][x] == c and not done[y1 + 1][x]:
                        y1 += 1
                for yy in range(y, y1 + 1):
                    for xx in range(x, x1 + 1):
                        done[yy][xx] = True
                draw.append({"dp": [x, y, c]} if (x, y) == (x1, y1) else {"dl": [x, y, x1, y1, c]})
        return draw

    def _app(self, grid, icon=None):
        app = {"draw": self._draw_commands(grid), "lifetime": self.lifetime, "lifetimeMode": 0}
        if icon:
            app["icon"] = icon
        return app

    def session(self, used, until):
        used = min(max(used, 0), 100)
        left = 100 - used
        icon, text, color = (("clawd_sad", "100%", RED) if left <= 0 else
                             ("clawd_panic", f"{used}%", RED) if left < 10 else
                             ("clawd", f"{used}%", CYAN))
        grid = self._grid()
        self._session_bar(grid, until)
        self._text(grid, 31 - self._text_width(text), 1, text, color)
        return self._app(grid, icon)

    def week(self, used, until):
        used = min(max(used, 0), 100)
        grid = self._grid()
        self._text(grid, 0, 1, "WEEK", ORANGE)
        color = RED if used > 90 else CYAN
        digits = str(used)
        self._text(grid, 25 - self._text_width(digits), 1, digits, color)
        self._text(grid, 26, 1, "%", color)
        fraction = 1 - min(max(until.total_seconds() / 3600 / 168, 0), 1) if until else 0
        self._segmented_bar(grid, fraction, 7, 3)
        return self._app(grid)

    def celebration(self):
        grid = self._grid()
        tick = int(time.time() * 1000) // 800
        confetti = [CYAN, PURPLE, ORANGE]
        for i in range(7):
            grid[(i * 3 + tick) % 7][13 + (i * 5 + tick * 3) % 18] = confetti[i % 3]
        legs = "..O..O.." if tick % 2 == 0 else ".O....O."
        self._sprite(grid, 1, 1, [".OOOOOO.", ".OBOOBO.", "OOOOOOOO", ".OOOOOO.", legs],
                     {"O": ORANGE, "B": "#2ea8ff"})
        return self._app(grid)

    def data_issue(self, used, until):
        used = min(max(used, 0), 100)
        grid = self._grid()
        self._session_bar(grid, until, GREY, DIM_GREY)
        text = f"{used}%"
        self._text(grid, 31 - self._text_width(text), 1, text, GREY)
        self._text(grid, 15, 1, "!", ORANGE)
        return self._app(grid, "clawd_grey")


# --- Worker ----------------------------------------------------------------------------

def sound_allowed(windows, at=None):
    """True when the local time lies inside the day's [start, end] window (Monday first)."""
    at = at or datetime.now()
    hour = at.hour + at.minute / 60
    start, end = windows[at.weekday()]
    if start <= hour < min(end, 24):
        return True
    # After midnight: still inside the previous day's window if it runs past 24.
    _, prev_end = windows[(at.weekday() - 1) % 7]
    return hour < prev_end - 24


def until_reset(resets_at):
    if resets_at and resets_at > now():
        return resets_at - now()
    return None


def is_window_reset(prev_pct, prev_reset, cur_pct, cur_reset):
    if prev_pct <= 0 or cur_pct >= prev_pct:
        return False
    return cur_reset is None or prev_reset is None or cur_reset > prev_reset


class Monitor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.clock = Awtrix(cfg["AwtrixHost"])
        self.poll_seconds = cfg["PollIntervalMinutes"] * 60
        self.screens = Screens(self.poll_seconds * 3)
        self.snapshot = None
        self.stale = True
        self.failures = 0
        self.paused_until = None
        self.celebrate_until = 0.0
        self.celebrating = False
        self.rotation_disabled = False
        self.icons_uploaded = False
        self.last_refresh = 0.0
        self.active = False
        self.lock = threading.Lock()

    def run(self):
        log.info("Monitoring usage every %s min, pushing to clock at %s",
                 self.cfg["PollIntervalMinutes"], self.cfg["AwtrixHost"])
        threading.Thread(target=self.peek_at_week, daemon=True).start()
        self.remove_screens()
        next_poll = 0.0
        while True:
            if time.monotonic() >= next_poll:
                next_poll = time.monotonic() + self.poll_seconds
                try:
                    with self.lock:
                        self.poll_once()
                except Exception:
                    log.exception("Poll cycle failed; retrying on next tick")
            active = thinking_active()
            if active != self.active:
                self.active = active
                try:
                    with self.lock:
                        self.on_thinking_changed()
                except Exception:
                    log.exception("Showing or hiding the Claude screens failed")
            time.sleep(2)

    def on_thinking_changed(self):
        if not self.active:
            log.info("Claude finished - hiding screens")
            self.remove_screens()
            return
        log.info("Claude is thinking - showing screens")
        self.push_screens()
        self.clock.switch_to(ISSUE_APP if self.stale else SESSION_APP)

    def remove_screens(self):
        for app in (SESSION_APP, WEEK_APP, ISSUE_APP):
            self.clock.remove_app(app)

    def peek_at_week(self):
        interval, seconds = self.cfg["WeekPeekIntervalMinutes"] * 60, self.cfg["WeekPeekSeconds"]
        if interval <= 0 or seconds <= 0:
            return
        while True:
            time.sleep(interval)
            try:
                if (not self.active or self.stale or time.time() < self.celebrate_until or
                        self.clock.current_app() != SESSION_APP or not self.clock.switch_to(WEEK_APP)):
                    continue
                time.sleep(seconds)
                if self.clock.current_app() == WEEK_APP:
                    self.clock.switch_to(SESSION_APP)
            except Exception:
                log.exception("Weekly peek failed")

    def poll_once(self):
        # Unlike the original, auto-rotation stays on so the Claude screens show up
        # between the clock's other apps without pressing buttons.
        if not self.icons_uploaded:
            self.icons_uploaded = all([self.clock.upload_icon(n, g) for n, g in clawd_icons().items()])
            if self.icons_uploaded:
                log.info("Clawd icons uploaded to the clock")
        self.update_state()
        if self.active:
            self.push_screens()

    def mark_stale(self, reason):
        if not self.stale:
            log.info("Display is now stale: %s", reason)
        self.stale = True

    def update_state(self):
        creds = read_credentials(self.cfg["KeychainService"])
        if creds is None:
            return self.mark_stale("credentials missing or unreadable")
        token, expires_at = creds
        t = now()

        # The desktop app keeps its own login and never refreshes the keychain entry, so
        # let the Claude CLI refresh it with a tiny prompt (at most every 30 minutes).
        if expires_at <= t and self.cfg["AutoRefreshLogin"] and time.time() - self.last_refresh > 1800:
            self.last_refresh = time.time()
            refresh_login(self.cfg["ClaudeCli"])
            creds = read_credentials(self.cfg["KeychainService"]) or creds
            token, expires_at = creds

        # An expired token only means Claude Code is idle; keep the last reading,
        # minus windows that have reset since.
        if expires_at <= t:
            if self.snapshot is None:
                return self.mark_stale("access token expired and no usage has been fetched yet")
            s = self.snapshot
            if s["session_reset"] and s["session_reset"] <= t:
                s.update(session=0, session_reset=None)
            if s["week_reset"] and s["week_reset"] <= t:
                s.update(week=0, week_reset=None)
            self.stale, self.failures = False, 0
            return

        if self.paused_until:
            if t.timestamp() < self.paused_until:
                return
            log.info("Rate-limit pause ended; resuming usage polling")
            self.paused_until = None

        kind, value = get_usage(self.cfg["UsageBaseUrl"], token)
        if kind == "ok":
            if self.stale:
                log.info("Usage data is fresh: session %s%%, weekly %s%%", value["session"], value["week"])
            self.detect_window_reset(self.snapshot, value)
            self.snapshot, self.stale, self.failures = value, False, 0
        elif kind == "unauthorized":
            self.mark_stale("usage endpoint rejected the token (401/403)")
        elif kind == "ratelimited":
            pause = value if value and value > self.poll_seconds else self.poll_seconds
            log.info("Rate limited; pausing usage calls for %ss", pause)
            self.paused_until = t.timestamp() + pause
        else:
            self.failures += 1
            log.warning("Transient usage failure (%s), %s in a row", value, self.failures)
            if self.failures >= self.cfg["StaleAfterFailures"]:
                self.mark_stale(f"{self.failures} consecutive transient failures")

    def detect_window_reset(self, prev, cur):
        if prev is None:
            return
        session = is_window_reset(prev["session"], prev["session_reset"], cur["session"], cur["session_reset"])
        weekly = is_window_reset(prev["week"], prev["week_reset"], cur["week"], cur["week_reset"])
        if not (session or weekly) or not self.active:
            return
        log.info("%s window reset - celebrating", "Session" if session else "Weekly")
        self.celebrate_until = time.time() + self.cfg["CelebrationSeconds"]
        if not self.celebrating:
            self.celebrating = True
            threading.Thread(target=self.celebrate, daemon=True).start()

    def celebrate(self):
        try:
            if (self.cfg["CelebrationSound"] and self.cfg["CelebrationMelody"] and
                    sound_allowed(self.cfg["SoundWindows"])):
                self.clock.play_melody(self.cfg["CelebrationMelody"])
            while time.time() < self.celebrate_until and not self.stale and self.active:
                frame = self.screens.celebration()
                self.clock.push_app(SESSION_APP, frame)
                self.clock.push_app(WEEK_APP, frame)
                time.sleep(0.8)
            if self.active:
                self.push_live_screens()
        finally:
            self.celebrating = False

    def push_live_screens(self):
        s = self.snapshot or {"session": 0, "session_reset": None, "week": 0, "week_reset": None}
        self.clock.push_app(SESSION_APP, self.screens.session(s["session"], until_reset(s["session_reset"])))
        self.clock.push_app(WEEK_APP, self.screens.week(s["week"], until_reset(s["week_reset"])))

    def push_screens(self):
        s = self.snapshot or {"session": 0, "session_reset": None}
        if self.stale:
            self.clock.push_app(ISSUE_APP, self.screens.data_issue(s["session"], until_reset(s["session_reset"])))
            self.clock.remove_app(SESSION_APP)
            self.clock.remove_app(WEEK_APP)
            return
        self.clock.remove_app(ISSUE_APP)
        if time.time() >= self.celebrate_until:
            self.push_live_screens()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        Monitor(load_config()).run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
