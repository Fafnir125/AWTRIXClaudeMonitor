#!/usr/bin/env python3
"""Adds or removes the thinking_hook.sh entries in ~/.claude/settings.json.

Other settings and hooks are left untouched; a backup is written before any change.
Usage: python3 setup_hooks.py add|remove
"""
import json
import os
import shutil
import sys

SETTINGS = os.path.expanduser("~/.claude/settings.json")
HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "thinking_hook.sh")
EVENTS = {"UserPromptSubmit": "start", "Stop": "stop", "SessionEnd": "stop"}


def is_ours(group):
    return any("thinking_hook.sh" in h.get("command", "") for h in group.get("hooks", []))


def main(mode):
    settings = {}
    if os.path.exists(SETTINGS):
        with open(SETTINGS) as f:
            settings = json.load(f)
        shutil.copy2(SETTINGS, SETTINGS + ".bak")

    hooks = settings.setdefault("hooks", {})
    for event, arg in EVENTS.items():
        groups = [g for g in hooks.get(event, []) if not is_ours(g)]
        if mode == "add":
            groups.append({"hooks": [{"type": "command", "command": f'"{HOOK}" {arg}'}]})
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks")

    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print(f"Hooks {'added to' if mode == 'add' else 'removed from'} {SETTINGS} (backup: settings.json.bak)")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("add", "remove"):
        sys.exit(__doc__)
    main(sys.argv[1])
