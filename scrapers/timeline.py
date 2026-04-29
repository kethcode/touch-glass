"""
Scrape tweets from X/Twitter home timeline, bookmarks, and own profile via Playwright CDP.
"""
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from scrapers.cdp import ensure_page, scroll_down, scroll_to_top, wait, EXTRACT_TWEETS_JS
from db.schema import get_db, upsert_tweet, upsert_link, link_tweet_url

URL_RE = re.compile(r'https?://[^\s<>"\']+')
SCHEME_RE = re.compile(r'(?i)https?\s*:\s*/\s*/')
DOMAIN_URL_RE = re.compile(
    r'(?i)\b(?:https?://)?'
    r'([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    r'(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+'
    r'(?:/[^\s<>"\']*)?)'
)
TRAILING_PUNCT = ".,;:!?)>]}'\""
URL_STOP_TOKENS = {"...", "|", "*", "-", "->"}


def extract_urls_from_text(text: str) -> list[str]:
    if not text:
        return []
    urls = URL_RE.findall(text)
    return [u.rstrip(TRAILING_PUNCT) for u in urls if len(u) > 10]


def _looks_like_url_continuation(token: str, current: str, allow_short_alpha: bool = False) -> bool:
    if not token:
        return False
    cleaned = token.strip(TRAILING_PUNCT)
    if not cleaned or cleaned in URL_STOP_TOKENS:
        return False
    if cleaned.startswith(("@", "#")):
        return False
    if any(ch in cleaned for ch in "/?&=#%._-"):
        return True
    if allow_short_alpha and "/" in current and len(cleaned) <= 8 and cleaned.isalnum():
        return True
    return False


def _repair_spaced_scheme_url(raw: str) -> str | None:
    match = SCHEME_RE.search(raw)
    if not match:
        return None

    prefix = re.sub(r"\s+", "", match.group(0)).lower()
    tail = raw[match.end():].strip()
    if not tail:
        return None

    parts = []
    current = prefix
    for token in re.split(r"\s+", tail):
        cleaned = token.strip()
        if not cleaned or cleaned in URL_STOP_TOKENS:
            break
        if not parts:
            parts.append(cleaned)
            current += cleaned
            continue
        if not _looks_like_url_continuation(cleaned, current, allow_short_alpha=True):
            break
        parts.append(cleaned)
        current += cleaned

    if not parts:
        return None
    return prefix + "".join(parts)


def _find_domain_url(raw: str) -> str | None:
    match = DOMAIN_URL_RE.search(raw)
    if not match:
        return None

    value = match.group(1).rstrip(TRAILING_PUNCT)
    tail = raw[match.end():].strip()
    current = value
    for token in re.split(r"\s+", tail):
        cleaned = token.strip()
        if not _looks_like_url_continuation(cleaned, current):
            break
        value += cleaned.rstrip(TRAILING_PUNCT)
        current = value

    return "https://" + value


def _coerce_url(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip().replace("\u2026", " ... ")
    if not raw or len(raw) > 500:
        return None

    compact = _repair_spaced_scheme_url(raw)
    if compact is None:
        compact = raw.rstrip(TRAILING_PUNCT)
    if not compact.startswith(("http://", "https://")):
        compact = _find_domain_url(raw) or ""

    compact = compact.rstrip(TRAILING_PUNCT)
    if not compact:
        return None

    parsed = urlparse(compact)
    host = parsed.netloc.lower()
    if not parsed.scheme or not host or "." not in host:
        return None

    path = parsed.path or ""
    if host in {"x.com", "twitter.com", "mobile.twitter.com"}:
        if re.match(r"^/[^/]+/status/\d+", path):
            return None
        if path.startswith("/i/cards"):
            return None
        return None
    return compact


def _extract_dom_links(raw_links: list) -> list[str]:
    urls = []
    for item in raw_links or []:
        candidates = []
        if isinstance(item, dict):
            candidates.extend([
                item.get("expanded_url"),
                item.get("title"),
                item.get("text"),
                item.get("aria_label"),
                item.get("href"),
            ])
        else:
            candidates.append(str(item))

        for candidate in candidates:
            url = _coerce_url(candidate)
            if url:
                urls.append(url)
                break
    return urls


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen = set()
    out = []
    for url in urls:
        normalized = url.strip()
        key = normalized.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    return out


def _click_show_new_posts(page, source: str) -> bool:
    selectors = [
        "div[role='button']:has-text('Show')",
        "button:has-text('Show')",
        "span:has-text('Show')",
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            text = locator.inner_text(timeout=1000).lower()
            if "post" not in text and "tweet" not in text:
                continue
            locator.click(timeout=2000)
            wait(1)
            print(f"  [{source}] clicked '{text[:80]}'")
            return True
        except Exception:
            continue
    return False


def _page_diagnostics(page) -> dict:
    diagnostics = {}
    for key, getter in {
        "url": lambda: page.url,
        "title": page.title,
        "article_count": lambda: page.evaluate("document.querySelectorAll('article[data-testid=\"tweet\"]').length"),
        "body": lambda: page.evaluate("(document.body && document.body.innerText || '').slice(0, 500)"),
    }.items():
        try:
            value = getter()
            if isinstance(value, str):
                value = re.sub(r"\s+", " ", value).strip()
            diagnostics[key] = value
        except Exception as exc:
            diagnostics[key] = f"<error: {exc}>"
    return diagnostics


def _prepare_page(url: str, source: str):
    page = ensure_page(url, wait_time=3.0, force_navigate=True)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass
    try:
        page.bring_to_front()
    except Exception:
        pass
    _click_show_new_posts(page, source)
    try:
        scroll_to_top(page)
        wait(1)
    except Exception:
        pass
    try:
        page.wait_for_selector('article[data-testid="tweet"]', timeout=12000)
    except Exception:
        diag = _page_diagnostics(page)
        print(f"  [{source}] no tweet articles after navigation: {diag}")
    return page


def _scrape_page(url: str, source: str, pages: int) -> dict:
    """Generic tweet page scraper."""
    now = datetime.now(timezone.utc).isoformat()
    page = _prepare_page(url, source)

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
            tweet["all_links"] = _dedupe_urls(_extract_dom_links(dom_links) + text_links)
            all_tweets[tid] = tweet

        scroll_down(page)
        wait(1.5 + (pg * 0.3))

    if not all_tweets:
        diag = _page_diagnostics(page)
        print(f"  [{source}] extracted zero tweets: {diag}")

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
