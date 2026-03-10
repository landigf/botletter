#!/usr/bin/env python3
"""
install_schedule.py — Install macOS LaunchAgent for nightly newsletter + morning reminder.

Creates two LaunchAgents:
1. Nightly newsletter generation at 11 PM
2. Morning reminder at login/wake (RunAtLoad) — pings Telegram so you see it on the bus

No persistent listener needed — feedback is fetched from Telegram right before generating.
"""

import os
import sys
from pathlib import Path

NEWSLETTER_DIR = Path(__file__).parent.parent.resolve()
VENV_PYTHON = NEWSLETTER_DIR / ".venv" / "bin" / "python"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"

GENERATE_LABEL = "com.botletter.generate"
REMINDER_LABEL = "com.botletter.reminder"
SYNC_LABEL = "com.botletter.sync"

# Legacy label to clean up
LISTENER_LABEL = "com.gennaro.newsletter.listener"


def get_env_vars() -> dict[str, str]:
    """Collect required env vars."""
    env = {}
    for key in ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN"):
        val = os.environ.get(key, "")
        if not val:
            print(f"❌ {key} not set in environment. Set it first.")
            sys.exit(1)
        env[key] = val
    env["PYTHONUNBUFFERED"] = "1"
    return env


def write_plist(label: str, args: list[str], env: dict, calendar: dict | None = None, keep_alive: bool = False, run_at_load: bool = False, interval_seconds: int | None = None):
    """Write a LaunchAgent plist file."""
    plist_path = LAUNCH_AGENTS_DIR / f"{label}.plist"

    env_xml = ""
    for k, v in env.items():
        env_xml += f"""\
            <key>{k}</key>
            <string>{v}</string>
"""

    args_xml = ""
    for a in args:
        args_xml += f"            <string>{a}</string>\n"

    schedule_xml = ""
    if calendar:
        schedule_xml = """\
        <key>StartCalendarInterval</key>
        <dict>
"""
        for k, v in calendar.items():
            schedule_xml += f"""\
            <key>{k}</key>
            <integer>{v}</integer>
"""
        schedule_xml += "        </dict>"
    elif interval_seconds:
        schedule_xml = f"""\
        <key>StartInterval</key>
        <integer>{interval_seconds}</integer>"""

    keepalive_xml = ""
    if keep_alive:
        keepalive_xml = """\
        <key>KeepAlive</key>
        <true/>"""

    runatload_xml = ""
    if run_at_load:
        runatload_xml = """\
        <key>RunAtLoad</key>
        <true/>"""

    plist = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
{args_xml}\
    </array>
    <key>WorkingDirectory</key>
    <string>{NEWSLETTER_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
{env_xml}\
    </dict>
    {schedule_xml}
    {keepalive_xml}
    {runatload_xml}
    <key>StandardOutPath</key>
    <string>/tmp/{label}.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/{label}.err</string>
</dict>
</plist>
"""

    plist_path.write_text(plist)
    return plist_path


def main():
    print("📅 Installing newsletter schedule...\n")

    if not VENV_PYTHON.exists():
        print(f"❌ Virtual env not found at {VENV_PYTHON}")
        print("   Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt")
        sys.exit(1)

    env = get_env_vars()
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)

    python = str(VENV_PYTHON)
    main_py = str(NEWSLETTER_DIR / "main.py")

    # Unload existing (ignore errors) — includes legacy labels
    legacy_labels = [
        "com.gennaro.newsletter.generate",
        "com.gennaro.newsletter.reminder",
        "com.gennaro.newsletter.sync",
        LISTENER_LABEL,
    ]
    for label in (GENERATE_LABEL, REMINDER_LABEL, SYNC_LABEL, *legacy_labels):
        plist = LAUNCH_AGENTS_DIR / f"{label}.plist"
        if plist.exists():
            os.system(f"launchctl unload {plist} 2>/dev/null")
            if label != GENERATE_LABEL and label != REMINDER_LABEL and label != SYNC_LABEL:
                plist.unlink()
                print(f"🗑  Removed legacy agent: {label}")

    # 1. Nightly newsletter generation at 11 PM + RunAtLoad fallback
    #    Fetches pending feedback from Telegram, then generates & sends.
    #    RunAtLoad catches missed 11 PM runs (Mac was asleep).
    #    Idempotent: skips if already generated+sent, retries send if generated but not sent.
    gen_path = write_plist(
        GENERATE_LABEL,
        [python, main_py, "generate"],
        env,
        calendar={"Hour": 23, "Minute": 0},
        run_at_load=True,
    )
    os.system(f"launchctl load {gen_path}")
    print(f"✅ Nightly generation (11 PM + retry at wake) → {gen_path}")

    # 2. Periodic sync — fetches & processes Telegram commands every 5 min
    #    Handles /add_interest, /add_topic, /config etc. and replies.
    #    Also answers callback queries (reaction buttons) so users don't see a spinner.
    #    Single HTTP call, <1s, negligible battery.
    sync_path = write_plist(
        SYNC_LABEL,
        [python, main_py, "sync"],
        env,
        interval_seconds=300,  # 5 minutes
    )
    os.system(f"launchctl load {sync_path}")
    print(f"✅ Command sync (every 30 min) → {sync_path}")

    # 3. Morning reminder — checks every 30 min + at login
    #    Sends a short Telegram ping so you see the newsletter on the bus.
    #    The remind command has a 5 AM–2 PM guard, so it only actually sends
    #    during morning hours. Outside that window it silently skips.
    rem_path = write_plist(
        REMINDER_LABEL,
        [python, main_py, "remind"],
        env,
        interval_seconds=1800,  # 30 minutes — morning guard handles the rest
        run_at_load=True,
    )
    os.system(f"launchctl load {rem_path}")
    print(f"✅ Morning reminder (at wake/login) → {rem_path}")

    print("\nDone! Flow:")
    print("  🌙 11 PM — fetches your feedback, generates tomorrow's newsletter, sends it")
    print("  ☀️  Wake  — pings you on Telegram: 'your newsletter is waiting'")
    print("  🚌  Bus   — read it, tap reactions")
    print("  🔄  Every 30 min — processes /add_interest, /config etc. and replies")
    print("\nNo background processes. Zero battery impact when Mac is asleep.")
    print(f"\nLogs:")
    print(f"  /tmp/{GENERATE_LABEL}.log")
    print(f"  /tmp/{SYNC_LABEL}.log")
    print(f"  /tmp/{REMINDER_LABEL}.log")
    print("\nTo uninstall:")
    print(f"  launchctl unload {gen_path}")
    print(f"  launchctl unload {rem_path}")
    print(f"  launchctl unload {sync_path}")


if __name__ == "__main__":
    main()
