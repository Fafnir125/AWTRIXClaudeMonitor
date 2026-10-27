#!/usr/bin/env python3
"""Plays every screen the monitor can show on the clock, one after another.

Usage: python3 demo.py [seconds per screen]
"""
import sys
import time
from datetime import timedelta

import claude_code_monitor as m

APP = "claude_demo"
SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 8

cfg = m.load_config()
clock = m.Awtrix(cfg["AwtrixHost"])
screens = m.Screens(lifetime=120)

for name, gif in m.clawd_icons().items():
    clock.upload_icon(name, gif)

scenarios = [
    ("Session normal (45 %, Clawd walking)", lambda: screens.session(45, timedelta(hours=2))),
    ("Session almost used up (95 %, Clawd panicking)", lambda: screens.session(95, timedelta(minutes=40))),
    ("Session used up (100 %, Clawd crying)", lambda: screens.session(100, timedelta(minutes=10))),
    ("Week normal (52 %)", lambda: screens.week(52, timedelta(days=2))),
    ("Week critical (95 %, red)", lambda: screens.week(95, timedelta(hours=12))),
    ("No fresh data (grey with !)", lambda: screens.data_issue(45, timedelta(hours=2))),
]

try:
    for title, build in scenarios:
        print(title, flush=True)
        clock.push_app(APP, build())
        clock.switch_to(APP)
        time.sleep(SECONDS)

    sound = cfg["CelebrationSound"] and m.sound_allowed(cfg["SoundWindows"])
    print("Window reset celebration (confetti" + (" + melody)" if sound else ", melody muted right now)"), flush=True)
    if sound:
        clock.play_melody(cfg["CelebrationMelody"])
    end = time.time() + SECONDS * 1.5
    while time.time() < end:
        clock.push_app(APP, screens.celebration())
        clock.switch_to(APP)
        time.sleep(0.8)
finally:
    clock.remove_app(APP)
    print("Demo finished", flush=True)
