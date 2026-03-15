"""
Scrape followers and following lists via Playwright CDP.
"""
import os
from datetime import datetime, timezone

from scrapers.cdp import ensure_page, evaluate_json, scroll_down, wait, EXTRACT_USERS_JS
from db.schema import get_db, upsert_contact


def _scrape_user_list(url: str, relationship: str, max_scrolls: int = 100) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    page = ensure_page(url)
    wait(3)

    all_users = {}
    stall_count = 0
    prev_count = 0

    for scroll_num in range(max_scrolls):
        try:
            raw = page.evaluate(EXTRACT_USERS_JS)
        except Exception:
            wait(2)
            continue

        if raw and isinstance(raw, list):
            for user in raw:
                uname = user.get("username")
                if uname and uname not in all_users:
                    user["relationship"] = relationship
                    user["platform"] = "twitter"
                    user["scraped_at"] = now
                    all_users[uname] = user

        current_count = len(all_users)
        if current_count == prev_count:
            stall_count += 1
            if stall_count >= 5:
                page.evaluate("window.scrollBy(0, -800)")
                wait(0.5)
                scroll_down(page, 1600)
                wait(2)
                try:
                    raw2 = page.evaluate(EXTRACT_USERS_JS)
                except Exception:
                    raw2 = []
                new_after = sum(1 for u in (raw2 or []) if u.get("username") not in all_users)
                if new_after == 0:
                    print(f"  [{relationship}] No new users after recovery at scroll {scroll_num}")
                    break
                stall_count = 0
        else:
            stall_count = 0
        prev_count = current_count

        scroll_down(page)
        wait(1.0 + (scroll_num % 5) * 0.2)

        if scroll_num % 20 == 0 and scroll_num > 0:
            print(f"  [{relationship}] scroll {scroll_num}: {len(all_users)} users")

    conn = get_db()
    new_contacts = 0
    try:
        for user in all_users.values():
            if upsert_contact(conn, user):
                new_contacts += 1
        conn.commit()
    finally:
        conn.close()

    stats = {"task": relationship, "total_seen": len(all_users), "new_contacts": new_contacts}
    print(f"[{relationship}] {stats}")
    return stats


def scrape_followers(username: str = os.environ.get("TWITTER_USERNAME", ""), max_scrolls: int = 100) -> dict:
    return _scrape_user_list(f"https://x.com/{username}/followers", "follower", max_scrolls)


def scrape_following(username: str = os.environ.get("TWITTER_USERNAME", ""), max_scrolls: int = 100) -> dict:
    return _scrape_user_list(f"https://x.com/{username}/following", "following", max_scrolls)


def detect_mutuals():
    conn = get_db()
    try:
        mutuals = conn.execute("""
            SELECT c1.username FROM contacts c1
            JOIN contacts c2 ON c1.username = c2.username AND c1.id != c2.id
            WHERE c1.relationship = 'follower' AND c2.relationship = 'following'
            AND c1.platform = 'twitter' AND c2.platform = 'twitter'
        """).fetchall()
        for row in mutuals:
            conn.execute("UPDATE contacts SET relationship='mutual' WHERE username=? AND platform='twitter'", (row[0],))
        conn.commit()
        print(f"[mutuals] Marked {len(mutuals)} mutual connections")
        return len(mutuals)
    finally:
        conn.close()


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from db.schema import init_db
    init_db()
    mode = sys.argv[1] if len(sys.argv) > 1 else "both"
    if mode in ("followers", "both"):
        scrape_followers()
    if mode in ("following", "both"):
        scrape_following()
    if mode == "both":
        detect_mutuals()
