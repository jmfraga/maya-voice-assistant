#!/usr/bin/env python3
"""Health check for Maya voice assistant.

Run via cron every 5 minutes:
  */5 * * * * cd /home/jmfraga/voice_assistant && /usr/bin/python3 scripts/health_check.py

Only alerts on state changes (avoids spamming Telegram).
"""

import os
import sys
import json
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
LOCK_FILE = os.path.join(BASE_DIR, "data", ".maya.lock")
STATE_FILE = os.path.join(BASE_DIR, "data", ".health_state")
CONFIG_FILE = os.path.join(BASE_DIR, "config.yaml")
ADMIN_URL = "http://localhost:8085/"


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def check_maya() -> list[str]:
    """Return list of issues found (empty = healthy)."""
    issues = []

    # 1. Lock file / process
    if not os.path.isfile(LOCK_FILE):
        issues.append("Maya no esta corriendo (sin lock file)")
    else:
        try:
            pid = int(open(LOCK_FILE).read().strip())
            if not _is_running(pid):
                issues.append(f"Maya crasheo (PID {pid} muerto, lock file existe)")
        except (ValueError, IOError):
            issues.append("Lock file corrupto")

    # 2. Admin Flask responding
    try:
        req = urllib.request.Request(ADMIN_URL, method="HEAD")
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        issues.append("Admin web (puerto 8085) no responde")

    return issues


def send_telegram(message: str, config: dict):
    """Send alert via Telegram bot."""
    token = config.get("telegram", {}).get("bot_token", "")
    contacts = config.get("telegram", {}).get("contacts", {})
    if not token or not contacts:
        return

    chat_id = list(contacts.values())[0]
    data = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }).encode()

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Error enviando Telegram: {e}", file=sys.stderr)


def main():
    issues = check_maya()

    # Read previous state
    prev_state = ""
    if os.path.isfile(STATE_FILE):
        try:
            prev_state = open(STATE_FILE).read().strip()
        except IOError:
            pass

    current_state = "|".join(issues) if issues else "ok"

    # Only alert on state change
    if current_state != prev_state:
        try:
            import yaml
            with open(CONFIG_FILE) as f:
                config = yaml.safe_load(f) or {}
        except Exception:
            config = {}

        if issues:
            msg = "🔴 <b>Maya Health Alert</b>\n\n" + "\n".join(f"• {i}" for i in issues)
            send_telegram(msg, config)
        elif prev_state:
            send_telegram("✅ Maya de vuelta en linea", config)

    # Save state
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        f.write(current_state)


if __name__ == "__main__":
    main()
