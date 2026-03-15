"""
Scrape tweets from X/Twitter home timeline, bookmarks, and own profile via Playwright CDP.
"""
import os
import re
from datetime import datetime, timezone

from scrapers.cdp import ensure_page, evaluate_json, scroll_down, wait, EXTRACT_TWEETS_JS
from db.schema import get_db, upsert_tweet, upsert_link, link_tweet_url

URL_RE = re.compile(r'https?://\S+')


def extract_urls_from_text(text: str) -> list[str]:
    if not text:
        return []
    urls = URL_RE.findall(text)
    return [u.rstrip(".,;:!?)>]}") for u in urls if len(u) > 10]


def _scrape_page(url: str, source: str, pages: int) -> dict:
    """Generic tweet page scraper."""
    now = datetime.now(timezone.utc).isoformat()
    page = ensure_page(url)
    wait(2)

    all_tweets = {}
    total_links = 0

    for pg in range(pages):
        try:
            raw = page.evaluate(EXTRACT_TWEETS_JS)
        except Exception as e:
            print(f"  [{source}] eval error on page {pg}: {e}")
            wait(2)
            continue

        if not raw or not isinstance(raw, list):
            wait(2)
            continue

        for tweet in raw:
            tid = tweet.get("id")
            if not tid or tid in all_tweets:
                continue
            tweet["source"] = source
            tweet["platform"] = "twitter"
            tweet["scraped_at"] = now
            tweet["created_at"] = tweet.pop("timestamp", None)
            dom_links = tweet.pop("links", [])
            text_links = extract_urls_from_text(tweet.get("text", ""))
            tweet["all_links"] = list(set(dom_links + text_links))
            all_tweets[tid] = tweet

        scroll_down(page)
        wait(1.5 + (pg * 0.3))

    # Store
    conn = get_db()
    new_tweets = 0
    try:
        for tweet in all_tweets.values():
            links = tweet.pop("all_links", [])
            is_new = upsert_tweet(conn, tweet)
            if is_new:
                new_tweets += 1
            for url in links:
                if "t.co/" in url and len(url) < 30:
                    continue
                if re.match(r'https?://(x|twitter)\.com/\w+/status/', url):
                    continue
                link_id = upsert_link(conn, url)
                link_tweet_url(conn, tweet["id"], link_id)
                total_links += 1
        conn.commit()
    finally:
        conn.close()

    stats = {"task": source, "total_seen": len(all_tweets), "new_tweets": new_tweets, "links_found": total_links}
    print(f"[{source}] {stats}")
    return stats


def scrape_timeline(pages: int = 5) -> dict:
    return _scrape_page("https://x.com/home", "timeline", pages)


def scrape_bookmarks(pages: int = 8) -> dict:
    return _scrape_page("https://x.com/i/bookmarks", "bookmarks", pages)


def scrape_own_tweets(username: str = os.environ.get("TWITTER_USERNAME", ""), pages: int = 5) -> dict:
    return _scrape_page(f"https://x.com/{username}", "own", pages)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__file__), ".."))
    from db.schema import init_db
    init_db()
    mode = sys.argv[1] if len(sys.argv) > 1 else "timeline"
    {"timeline": scrape_timeline, "bookmarks": scrape_bookmarks, "own": scrape_own_tweets}[mode]()
