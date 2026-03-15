"""
Scrape LinkedIn feed and saved posts via Playwright CDP.
"""

import re
from datetime import datetime, timezone

from scrapers.cdp import ensure_page, scroll_down, wait
from db.schema import get_db, upsert_tweet, upsert_link, link_tweet_url


EXTRACT_LINKEDIN_POSTS_JS = """
() => {
    return [...document.querySelectorAll('.feed-shared-update-v2, .occludable-update')].map(post => {
        const authorEl = post.querySelector('.update-components-actor__title, .feed-shared-actor__title');
        const textEl = post.querySelector('.feed-shared-update-v2__description, .feed-shared-text, .break-words');
        const timeEl = post.querySelector('time, .feed-shared-actor__sub-description');

        const links = [];
        post.querySelectorAll('a[href]').forEach(a => {
            const href = a.href;
            if (href && !href.includes('linkedin.com/in/') && !href.includes('linkedin.com/feed')
                && !href.includes('#') && href.startsWith('http')) {
                links.push(href);
            }
        });

        const linkEl = post.querySelector('a[href*="activity"]') || post.querySelector('a[href*="ugcPost"]');
        const postUrl = linkEl ? linkEl.href : null;
        const idMatch = postUrl ? postUrl.match(/activity:(\\d+)|ugcPost:(\\d+)/) : null;
        const postId = idMatch ? (idMatch[1] || idMatch[2]) : null;

        const likesEl = post.querySelector('.social-details-social-counts__reactions-count');

        return {
            id: postId || ('li_' + Math.random().toString(36).slice(2, 10)),
            author_display_name: authorEl ? authorEl.innerText.trim().split('\\n')[0] : '',
            text: textEl ? textEl.innerText.trim() : '',
            url: postUrl,
            timestamp: timeEl ? timeEl.innerText.trim() : null,
            likes: likesEl ? parseInt(likesEl.innerText.replace(/[^0-9]/g, '')) || 0 : 0,
            links: links,
        };
    }).filter(p => p.text && p.text.length > 10);
}
"""


def _scrape_linkedin(url: str, source: str, pages: int) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    page = ensure_page(url)
    wait(3)

    all_posts = {}
    total_links = 0

    for pg in range(pages):
        try:
            raw = page.evaluate(EXTRACT_LINKEDIN_POSTS_JS)
        except Exception as e:
            print(f"  [{source}] eval error: {e}")
            wait(2)
            continue

        if raw and isinstance(raw, list):
            for post in raw:
                pid = post.get("id")
                if not pid or pid in all_posts:
                    continue
                post["source"] = source
                post["platform"] = "linkedin"
                post["scraped_at"] = now
                raw_ts = post.pop("timestamp", None)
                # Only keep created_at if it's an ISO-like timestamp
                if raw_ts and raw_ts.startswith("202"):
                    post["created_at"] = raw_ts
                else:
                    post["created_at"] = now  # Use scrape time as fallback
                post["author_username"] = ""
                dom_links = post.pop("links", [])
                post["all_links"] = dom_links
                post["retweets"] = 0
                post["replies"] = 0
                post["views"] = 0
                all_posts[pid] = post

        scroll_down(page, 1200)
        wait(2)

    conn = get_db()
    new_posts = 0
    try:
        for post in all_posts.values():
            links = post.pop("all_links", [])
            if upsert_tweet(conn, post):
                new_posts += 1
            for url in links:
                link_id = upsert_link(conn, url)
                link_tweet_url(conn, post["id"], link_id)
                total_links += 1
        conn.commit()
    finally:
        conn.close()

    stats = {"task": source, "total_seen": len(all_posts), "new_posts": new_posts, "links_found": total_links}
    print(f"[{source}] {stats}")
    return stats


def scrape_linkedin_feed(pages: int = 5) -> dict:
    return _scrape_linkedin("https://linkedin.com/feed", "linkedin_feed", pages)


def scrape_linkedin_saved(pages: int = 5) -> dict:
    return _scrape_linkedin("https://linkedin.com/my-items/saved-posts", "linkedin_bookmarks", pages)


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from db.schema import init_db
    init_db()
    mode = sys.argv[1] if len(sys.argv) > 1 else "feed"
    if mode == "feed":
        scrape_linkedin_feed()
    elif mode == "saved":
        scrape_linkedin_saved()
    elif mode == "both":
        scrape_linkedin_feed()
        scrape_linkedin_saved()
