#!/usr/bin/env python3
"""
Always-on brain monitor.

Continuously cycles through scraping tasks:
1. Timeline tweets (every cycle)
2. Bookmarks (every cycle)
3. Own tweets (every 3rd cycle)
4. Followers (every 6th cycle — slow, lots of scrolling)
5. Following (every 6th cycle, offset)
6. LinkedIn feed (every 2nd cycle)
7. LinkedIn saved (every 3rd cycle)
8. Link enrichment (every cycle)
9. Embeddings (every 4th cycle)

Each cycle takes ~5-10 min depending on scroll depth.
Runs indefinitely until killed.
"""

import sys
import os
import random
import time
import traceback
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db.schema import init_db, get_db


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def env_bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def env_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        log(f"Invalid {name}={raw!r}; using {default}")
        return default
    if minimum is not None:
        value = max(minimum, value)
    return value


def sleep_between(base_seconds: float, jitter_seconds: float = 0):
    delay = base_seconds
    if jitter_seconds > 0:
        delay += random.uniform(0, jitter_seconds)
    time.sleep(delay)


def run_task(name: str, fn, **kwargs) -> dict | None:
    """Run a scraping task with error handling."""
    log(f"Starting: {name}")
    try:
        result = fn(**kwargs)
        log(f"Done: {name} -> {result}")
        return result
    except Exception as e:
        log(f"FAILED: {name} -> {e}")
        traceback.print_exc()
        # Log to DB
        try:
            conn = get_db()
            conn.execute("""
                INSERT INTO scrape_log (task, status, error, started_at, finished_at)
                VALUES (?, 'failed', ?, ?, ?)
            """, (name, str(e)[:500],
                  datetime.now(timezone.utc).isoformat(),
                  datetime.now(timezone.utc).isoformat()))
            conn.commit()
            conn.close()
        except Exception:
            pass
        return None


def main():
    log("Initializing brain database...")
    init_db()

    # Import scrapers lazily (so CDP connection only happens when needed)
    from scrapers.timeline import scrape_timeline, scrape_bookmarks, scrape_own_tweets
    from scrapers.followers import scrape_followers, scrape_following, detect_mutuals
    from scrapers.linkedin import scrape_linkedin_feed, scrape_linkedin_saved
    from scrapers.telegram import scrape_telegram_groups
    from enrichment.links import enrich_pending_links

    enable_timeline = env_bool("TOUCH_GLASS_ENABLE_TIMELINE", True)
    enable_bookmarks = env_bool("TOUCH_GLASS_ENABLE_BOOKMARKS", True)
    enable_own_tweets = env_bool("TOUCH_GLASS_ENABLE_OWN_TWEETS", True)
    enable_followers = env_bool("TOUCH_GLASS_ENABLE_FOLLOWERS", True)
    enable_linkedin = env_bool("TOUCH_GLASS_ENABLE_LINKEDIN", True)
    enable_telegram = env_bool("TOUCH_GLASS_ENABLE_TELEGRAM", True)
    enable_digest = env_bool("TOUCH_GLASS_ENABLE_DIGEST", True)

    # Optional: embeddings (OpenAI or local llama.cpp-compatible server)
    embed_available = False
    try:
        from enrichment.embeddings import (
            embed_tweets,
            embed_links,
            embed_contacts,
            embeddings_enabled,
            embedding_status,
        )
        status = embedding_status()
        embed_available = embeddings_enabled()
        if embed_available:
            log(f"Embeddings enabled ({status['provider']}: {status['model']})")
        else:
            missing = ", ".join(status.get("missing", []))
            suffix = f"; missing {missing}" if missing else ""
            log(f"Embeddings disabled ({status['provider']}{suffix})")
    except Exception as e:
        log(f"Embeddings disabled ({e})")

    # Digest
    from enrichment.digest import send_digest
    last_digest_date = None

    cycle = 0
    cycle_interval = env_int("TOUCH_GLASS_CYCLE_SECONDS", 600, minimum=60)
    cycle_jitter = env_int("TOUCH_GLASS_CYCLE_JITTER_SECONDS", 180, minimum=0)
    task_jitter = env_int("TOUCH_GLASS_TASK_JITTER_SECONDS", 7, minimum=0)

    log(f"Monitor started. Cycle interval: {cycle_interval}s (+0-{cycle_jitter}s jitter)")
    enabled = []
    if enable_timeline:
        enabled.append("timeline")
    if enable_bookmarks:
        enabled.append("bookmarks")
    if enable_own_tweets:
        enabled.append("own tweets")
    if enable_followers:
        enabled.append("followers/following")
    if enable_linkedin:
        enabled.append("LinkedIn")
    if enable_telegram:
        enabled.append("Telegram")
    enabled.append("links")
    log("Scraping: " + ", ".join(enabled))
    log("Press Ctrl+C to stop")
    print("=" * 60, flush=True)

    while True:
        cycle += 1
        cycle_start = time.time()
        log(f"=== CYCLE {cycle} ===")

        # --- Twitter ---

        # Timeline: every cycle (5 pages = ~50 tweets)
        if enable_timeline:
            run_task("twitter_timeline", scrape_timeline, pages=5)
            sleep_between(3, task_jitter)

        # Bookmarks: every cycle (8 pages deep)
        if enable_bookmarks:
            run_task("twitter_bookmarks", scrape_bookmarks, pages=8)
            sleep_between(3, task_jitter)

        # Own tweets: every 3rd cycle
        if enable_own_tweets and cycle % 3 == 0:
            run_task("twitter_own", scrape_own_tweets, pages=5)
            sleep_between(3, task_jitter)

        # Followers: every 6th cycle (deep scroll)
        if enable_followers and cycle % 6 == 1:
            run_task("twitter_followers", scrape_followers, max_scrolls=80)
            sleep_between(5, task_jitter)

        # Following: every 6th cycle (offset by 3)
        if enable_followers and cycle % 6 == 4:
            run_task("twitter_following", scrape_following, max_scrolls=80)
            sleep_between(5, task_jitter)
            run_task("detect_mutuals", detect_mutuals)

        # --- LinkedIn ---

        # LinkedIn feed: every 2nd cycle
        if enable_linkedin and cycle % 2 == 0:
            run_task("linkedin_feed", scrape_linkedin_feed, pages=5)
            sleep_between(3, task_jitter)

        # LinkedIn saved: every 3rd cycle
        if enable_linkedin and cycle % 3 == 0:
            run_task("linkedin_saved", scrape_linkedin_saved, pages=5)
            sleep_between(3, task_jitter)

        # --- Telegram ---

        # Telegram groups: every 3rd cycle (~30 min), all groups
        if enable_telegram and cycle % 3 == 0:
            run_task("telegram_groups", scrape_telegram_groups, max_groups=200, scroll_pages=3)
            sleep_between(3, task_jitter)

        # --- Enrichment ---

        # Link enrichment: every cycle (batch of 30)
        run_task("link_enrichment", enrich_pending_links, batch_size=30)

        # Embeddings: every 4th cycle
        if embed_available and cycle % 4 == 0:
            run_task("embed_tweets", embed_tweets, batch_size=200)
            run_task("embed_links", embed_links, batch_size=100)
            run_task("embed_contacts", embed_contacts, batch_size=100)

        # --- Daily Digest (send once per day, around 7am or on first cycle) ---
        today = datetime.now().strftime("%Y-%m-%d")
        hour = datetime.now().hour
        if enable_digest and last_digest_date != today and (hour >= 7 or cycle == 1):
            run_task("daily_digest", send_digest)
            last_digest_date = today

        # --- Stats ---
        try:
            conn = get_db()
            tweet_count = conn.execute("SELECT COUNT(*) FROM tweets").fetchone()[0]
            link_count = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
            contact_count = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
            pending_links = conn.execute("SELECT COUNT(*) FROM links WHERE fetch_status='pending'").fetchone()[0]
            conn.close()
            elapsed = int(time.time() - cycle_start)
            log(f"Stats: {tweet_count} tweets, {link_count} links ({pending_links} pending), {contact_count} contacts")
            log(f"Cycle {cycle} completed in {elapsed}s")
        except Exception:
            pass

        print("=" * 60, flush=True)

        # Sleep until next cycle
        elapsed = time.time() - cycle_start
        sleep_time = max(60, cycle_interval - elapsed)
        if cycle_jitter > 0:
            sleep_time += random.uniform(0, cycle_jitter)
        log(f"Sleeping {int(sleep_time)}s until next cycle...")
        time.sleep(sleep_time)


if __name__ == "__main__":
    main()
