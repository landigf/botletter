#!/usr/bin/env python3
"""
main.py — Botletter: adaptive daily science newsletter

Commands:
    python main.py generate              # Generate & send today's newsletter
    python main.py generate --no-fetch   # LLM-only (no arXiv, $0 API cost)
    python main.py generate --no-send    # Generate but don't send to Telegram
    python main.py listen                # Start Telegram bot (listens for feedback)
    python main.py setup                 # Interactive setup guide

Env vars needed:
    GEMINI_API_KEY       — free at https://aistudio.google.com/apikey
    TELEGRAM_BOT_TOKEN   — from @BotFather on Telegram
"""

import argparse
import asyncio
import os
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).parent


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def cmd_setup(args):
    """Interactive setup guide."""
    print("🔬 Botletter — Setup\n")

    # Check Gemini
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        print("✅ GEMINI_API_KEY is set")
    else:
        print("❌ GEMINI_API_KEY not set")
        print("   Get a free key: https://aistudio.google.com/apikey")
        print("   Then: export GEMINI_API_KEY='your-key'\n")

    # Check Telegram
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if token:
        print("✅ TELEGRAM_BOT_TOKEN is set")
    else:
        print("❌ TELEGRAM_BOT_TOKEN not set")
        print("   1. Open Telegram → @BotFather → /newbot")
        print("   2. Name it, get the token")
        print("   3. export TELEGRAM_BOT_TOKEN='your-token'\n")

    # Check chat ID
    from store import load_telegram_state
    state = load_telegram_state()
    if state.get("chat_id"):
        print(f"✅ Telegram chat registered (ID: {state['chat_id']})")
    else:
        print("❌ No Telegram chat registered")
        print("   Run: python main.py listen")
        print("   Then send /start to your bot in Telegram\n")

    if key and token and state.get("chat_id"):
        print("\n🎉 All set! Run: python main.py generate")


def _newsletter_date() -> str:
    """If it's evening (6 PM+), target tomorrow's date — the issue you'll read in the morning."""
    now = datetime.now()
    if now.hour >= 18:
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")


def cmd_generate(args):
    """Generate and optionally send the daily newsletter."""
    config = load_config()
    date = args.date or _newsletter_date()
    output_dir = ROOT / config["output"]["directory"]
    filename = config["output"]["filename_format"].format(date=date)
    output_path = output_dir / filename

    from store import mark_sent, was_sent

    # Idempotency: fully done (generated + sent) → skip
    if output_path.exists() and was_sent(date) and not args.date:
        print(f"📬 Newsletter for {date} already delivered. Skipping.")
        return

    # Generated but not sent (e.g. no WiFi last night) → retry sending only
    if output_path.exists() and not was_sent(date) and not args.date:
        print(f"📝 Newsletter for {date} exists but wasn't sent. Retrying delivery...")
        _try_send(date, output_path, config, mark_sent)
        return

    # Validate API key
    if not os.environ.get("GEMINI_API_KEY", ""):
        print("❌ Set GEMINI_API_KEY (free at https://aistudio.google.com/apikey)")
        sys.exit(1)

    from store import (
        append_to_knowledge_map,
        clear_more_requests,
        get_target_word_count,
        load_history,
        save_history,
    )
    from templates import get_section_word_counts

    # ── Fetch pending feedback from Telegram before generating ──
    if os.environ.get("TELEGRAM_BOT_TOKEN", ""):
        from bot import fetch_pending_feedback
        try:
            n = fetch_pending_feedback()
            if n:
                print(f"📥 Processed {n} feedback update(s) from Telegram")
        except Exception as e:
            print(f"⚠️  Could not fetch feedback: {e}")

    is_sunday = datetime.strptime(date, "%Y-%m-%d").weekday() == 6

    history = load_history()
    total_words = get_target_word_count(config)
    word_counts = get_section_word_counts(total_words, is_sunday)

    # ── Fetch papers ──
    research_paper = None
    thesis_paper = None

    if not args.no_fetch:
        from fetcher import fetch_papers
        print("📡 Fetching arXiv papers...")
        papers = fetch_papers(config, history.get("papers_seen", []))
        print(f"   {len(papers['research_papers'])} general + {len(papers['thesis_papers'])} thesis-related")

        if papers["research_papers"]:
            research_paper = papers["research_papers"][0]
        if papers["thesis_papers"]:
            thesis_paper = papers["thesis_papers"][0]

    # ── Pick curiosity theme ──
    themes = config.get("curiosity_themes", [])
    used = history.get("themes_used", [])
    available = [t for t in themes if t not in used[-(len(themes) - 2):]]
    theme = random.choice(available or themes)

    # ── Generate sections ──
    from generator import NewsletterGenerator

    print("✨ Generating newsletter...")
    gen = NewsletterGenerator(config)
    sections = {}
    knowledge_entries = []

    print(f"   💡 Curiosity ({theme})...")
    sections["curiosity"] = gen.curiosity(theme, word_counts["curiosity"])
    knowledge_entries.append(f"Curiosity: {theme}")

    if research_paper:
        title_short = research_paper["title"][:60]
        print(f"   📄 Research: {title_short}...")
        sections["research"] = gen.research_spotlight(research_paper, word_counts["research"])
        knowledge_entries.append(f"Paper: {research_paper['title']}")

    print("   ⚡ Quick bites...")
    sections["quick_bites"] = gen.quick_bites(
        history.get("quick_bite_topics", []), word_counts["quick_bites"]
    )

    print("   🎯 Research corner...")
    sections["thesis_corner"] = gen.thesis_corner(thesis_paper, word_counts["thesis_corner"])
    if thesis_paper:
        knowledge_entries.append(f"Thesis: {thesis_paper['title']}")

    # Sunday recap
    if is_sunday and config.get("schedule", {}).get("sunday_recap", True):
        print("   🔄 Weekly recap...")
        from store import load_knowledge_map
        km = load_knowledge_map()
        recent_lines = [l.strip("- \n") for l in km.split("\n") if l.startswith("- ")][-20:]
        if recent_lines:
            sections["recap"] = gen.recap(recent_lines, word_counts.get("recap", 300))
            knowledge_entries.append("Weekly recap")

    # ── Save markdown ──
    output_dir.mkdir(parents=True, exist_ok=True)

    newsletter_md = _assemble_markdown(date, sections, research_paper, thesis_paper)
    output_path.write_text(newsletter_md)

    # ── Update history ──
    history["themes_used"].append(theme)
    if research_paper:
        history["papers_seen"].append(research_paper["id"])
    if thesis_paper:
        history["papers_seen"].append(thesis_paper["id"])
    save_history(history)

    # ── Update knowledge map ──
    append_to_knowledge_map(date, knowledge_entries)
    clear_more_requests()

    print(f"\n📝 Saved: {output_path}")

    # ── Send to Telegram ──
    if not args.no_send:
        _try_send(date, output_path, config, mark_sent)

    # ── Publish to website ──
    _publish_site(config)

    print("Done!")


def _try_send(date: str, output_path, config: dict, mark_sent):
    """Attempt to send the newsletter to Telegram. Marks sent on success."""
    from bot import get_chat_id
    if not get_chat_id():
        print("⚠️  No Telegram chat registered. Run 'python main.py listen' and send /start to the bot.")
        return

    # Reload sections from the saved markdown to extract topic hints
    # (for retry case where we don't have sections in memory)
    try:
        print("📱 Sending to Telegram...")
        from bot import send_newsletter
        # Read saved markdown to rebuild sections for delivery
        md = output_path.read_text()
        sections = _parse_sections_from_markdown(md)
        # Use section names as topic hints (good enough for retry)
        paper_topics = list(sections.keys())
        asyncio.run(send_newsletter(date, sections, paper_topics))
        mark_sent(date)
        print("✅ Delivered!")

        # Publish to public channel if configured
        channel = config.get("telegram", {}).get("channel_username")
        if channel:
            try:
                from bot import publish_to_channel
                asyncio.run(publish_to_channel(date, sections, channel))
                print("📢 Published to channel!")
            except Exception as e:
                print(f"⚠️  Channel publish failed: {e}")

    except Exception as e:
        print(f"❌ Send failed (no WiFi?): {e}")
        print("   Will retry on next Mac wake.")


def _publish_site(config: dict):
    """Rebuild the static site and push to GitHub Pages."""
    import subprocess

    try:
        from site_builder import build_site
        channel = config.get("telegram", {}).get("channel_username")
        build_site(channel)
    except Exception as e:
        print(f"⚠️  Site build failed: {e}")
        return

    # Auto-commit and push docs/
    try:
        subprocess.run(
            ["git", "add", "docs/"],
            cwd=str(ROOT), capture_output=True, check=True,
        )
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet", "docs/"],
            cwd=str(ROOT), capture_output=True,
        )
        if result.returncode == 0:
            # No changes to commit
            return
        subprocess.run(
            ["git", "commit", "-m", "site: update newsletter archive"],
            cwd=str(ROOT), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "push"],
            cwd=str(ROOT), capture_output=True, check=True,
        )
        print("🚀 Website updated and pushed to GitHub")
    except Exception as e:
        print(f"⚠️  Git push failed (no WiFi?): {e}")


def _parse_sections_from_markdown(md: str) -> dict[str, str]:
    """Extract sections from a saved newsletter markdown file."""
    section_map = {
        "💡 Deep Curiosity": "curiosity",
        "📄 Research Spotlight": "research",
        "⚡ Quick Bites": "quick_bites",
        "🎯 Your Research Corner": "thesis_corner",
        "🔄 Weekly Recap": "recap",
    }
    sections = {}
    current_key = None
    current_lines = []

    for line in md.split("\n"):
        if line.startswith("## "):
            # Save previous section
            if current_key:
                sections[current_key] = "\n".join(current_lines).strip()
            # Check if this heading matches a section
            heading = line[3:].strip()
            current_key = section_map.get(heading)
            current_lines = []
        elif line.strip() == "---":
            if current_key:
                sections[current_key] = "\n".join(current_lines).strip()
                current_key = None
                current_lines = []
        elif current_key is not None:
            current_lines.append(line)

    if current_key:
        sections[current_key] = "\n".join(current_lines).strip()

    return sections


def cmd_remind(args):
    """Send a short morning reminder to check the newsletter."""
    # Only send during morning hours (5 AM - 2 PM)
    hour = datetime.now().hour
    if hour < 5 or hour >= 14:
        print(f"⏭  Not morning ({hour}:00). Skipping reminder.")
        return

    if not os.environ.get("TELEGRAM_BOT_TOKEN", ""):
        print("❌ Set TELEGRAM_BOT_TOKEN")
        sys.exit(1)

    from bot import get_chat_id
    if not get_chat_id():
        print("⚠️  No chat registered. Send /start to the bot first.")
        return

    # Only remind if today's newsletter actually exists
    config = load_config()
    date = datetime.now().strftime("%Y-%m-%d")
    output_dir = ROOT / config["output"]["directory"]
    filename = config["output"]["filename_format"].format(date=date)
    if not (output_dir / filename).exists():
        print(f"⏭  No newsletter for today ({date}). Skipping reminder.")
        return

    # Idempotency: only remind once per day
    reminder_file = ROOT / config["output"]["data_directory"] / "last_reminder.txt"
    if reminder_file.exists() and reminder_file.read_text().strip() == date:
        print(f"⏭  Already reminded for {date}. Skipping.")
        return

    from bot import send_reminder
    send_reminder()
    reminder_file.parent.mkdir(parents=True, exist_ok=True)
    reminder_file.write_text(date)
    print("☀️ Morning reminder sent!")


def cmd_listen(args):
    """Run the Telegram bot to listen for feedback (optional, for manual use)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("❌ Set TELEGRAM_BOT_TOKEN (from @BotFather)")
        sys.exit(1)

    from bot import run_listener
    run_listener()


def cmd_sync(args):
    """Fetch and process pending Telegram commands (add/remove interests etc.)."""
    if not os.environ.get("TELEGRAM_BOT_TOKEN", ""):
        return
    from bot import fetch_pending_feedback
    try:
        n = fetch_pending_feedback()
        if n:
            print(f"📥 Processed {n} Telegram update(s)")
    except Exception as e:
        print(f"⚠️  Sync failed: {e}")


def _assemble_markdown(date: str, sections: dict, research_paper: dict | None, thesis_paper: dict | None) -> str:
    """Assemble sections into a full newsletter markdown file."""
    weekday = datetime.strptime(date, "%Y-%m-%d").strftime("%A")
    parts = [
        f"# 🔬 Daily Science — {weekday}, {date}\n",
        "*Feed your curiosity*\n",
        "---\n",
    ]

    if "curiosity" in sections:
        parts.append("## 💡 Deep Curiosity\n")
        parts.append(sections["curiosity"].strip() + "\n")

    if "research" in sections:
        parts.append("---\n")
        parts.append("## 📄 Research Spotlight\n")
        parts.append(sections["research"].strip() + "\n")
        if research_paper:
            parts.append(f"> [Read the paper]({research_paper['pdf_url']})\n")

    if "quick_bites" in sections:
        parts.append("---\n")
        parts.append("## ⚡ Quick Bites\n")
        parts.append(sections["quick_bites"].strip() + "\n")

    if "thesis_corner" in sections:
        parts.append("---\n")
        parts.append("## 🎯 Your Research Corner\n")
        parts.append(sections["thesis_corner"].strip() + "\n")
        if thesis_paper:
            parts.append(f"> [Read the paper]({thesis_paper['pdf_url']})\n")

    if "recap" in sections:
        parts.append("---\n")
        parts.append("## 🔄 Weekly Recap\n")
        parts.append(sections["recap"].strip() + "\n")

    parts.append("---\n")
    parts.append("*Stay curious.*\n")

    return "\n\n".join(parts) + "\n"


def main():
    parser = argparse.ArgumentParser(
        description="Botletter — adaptive daily science newsletter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # generate
    gen = sub.add_parser("generate", help="Generate today's newsletter")
    gen.add_argument("--date", help="Date (YYYY-MM-DD), default: today")
    gen.add_argument("--no-fetch", action="store_true", help="Skip arXiv, LLM-only")
    gen.add_argument("--no-send", action="store_true", help="Don't send to Telegram")
    gen.set_defaults(func=cmd_generate)

    # remind
    rem = sub.add_parser("remind", help="Send morning Telegram reminder")
    rem.set_defaults(func=cmd_remind)

    # sync (periodic command processing)
    syn = sub.add_parser("sync", help="Fetch & process pending Telegram commands")
    syn.set_defaults(func=cmd_sync)

    # listen (optional — for manual use or initial /start registration)
    lst = sub.add_parser("listen", help="Start Telegram bot for /start registration")
    lst.set_defaults(func=cmd_listen)

    # setup
    stp = sub.add_parser("setup", help="Interactive setup guide")
    stp.set_defaults(func=cmd_setup)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
