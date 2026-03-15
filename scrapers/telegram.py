"""
Scrape Telegram Web chats via Chrome CDP.

- Extracts messages from group chats and DMs
- Stores individual messages + conversation snippets
- Extracts links as first-class entities
"""

import json
import os
import re
import time
from datetime import datetime, timezone

from scrapers.cdp import _get_browser, wait
from db.schema import get_db, upsert_link, link_tweet_url

URL_RE = re.compile(r'https?://\S+')

EXTRACT_MESSAGES_JS = """() => {
    const msgs = document.querySelectorAll('.Message.message-list-item');
    const result = [];
    const seen = new Set();
    msgs.forEach(msg => {
        const msgId = msg.getAttribute('data-message-id') || '';
        if (!msgId || seen.has(msgId)) return;
        seen.add(msgId);

        const text = msg.innerText || '';
        if (text.length < 5) return;

        // Author from sender name element
        const authorEl = msg.querySelector('[class*="sender-title"], [class*="message-title"], .message-peer-name');
        const author = authorEl ? authorEl.innerText.trim() : '';

        // Time from meta
        const timeEl = msg.querySelector('[class*="MessageMeta"] time, [class*="message-time"]');
        const timestamp = timeEl ? (timeEl.getAttribute('datetime') || timeEl.innerText.trim()) : '';

        // Text content (without time/author)
        const contentEl = msg.querySelector('[class*="text-content"], .text-content');
        const content = contentEl ? contentEl.innerText.trim() : text.split('\\n').filter(l => l.length > 2).join(' ').slice(0, 2000);

        // Extract links
        const links = [];
        msg.querySelectorAll('a[href]').forEach(a => {
            const href = a.href;
            if (href && href.startsWith('http') && !href.includes('web.telegram.org')) {
                links.push(href);
            }
        });

        // Check if own message
        const isOwn = msg.className.includes(' own');

        result.push({
            id: 'tg_' + msgId,
            author: author || (isOwn ? '_self_' : ''),
            text: content.slice(0, 2000),
            time: timestamp,
            links: links,
            hasLink: links.length > 0,
            isOwn: isOwn,
        });
    });
    return result;
}"""


def scrape_chat(page, chat_name: str, chat_type: str = "group", scroll_pages: int = 3) -> dict:
    """Scrape messages from the currently open chat."""
    now = datetime.now(timezone.utc).isoformat()
    all_messages = {}

    for pg in range(scroll_pages):
        try:
            raw = page.evaluate(EXTRACT_MESSAGES_JS)
        except Exception as e:
            print(f"  [{chat_name}] eval error: {e}")
            wait(1)
            continue

        if raw:
            for msg in raw:
                mid = msg.get("id") or f"tg_{hash(msg['author'] + msg['text'][:50])}"
                if mid not in all_messages:
                    msg["chat_name"] = chat_name
                    msg["chat_type"] = chat_type
                    msg["platform"] = "telegram"
                    msg["scraped_at"] = now
                    all_messages[mid] = msg

        # Scroll up to load older messages
        page.evaluate('() => { const c = document.querySelector("[class*=MessageList], [class*=message-list]"); if(c) c.scrollTop -= 800; }')
        wait(1.5)

    # Store messages
    conn = get_db()
    new_msgs = 0
    total_links = 0

    for mid, msg in all_messages.items():
        existing = conn.execute("SELECT id FROM messages WHERE id=?", (mid,)).fetchone()
        if existing:
            continue

        has_link = 1 if msg.get("links") else 0
        conn.execute("""
            INSERT OR IGNORE INTO messages (id, platform, chat_name, chat_type, author, text,
                has_link, url, scraped_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            mid, "telegram", chat_name, chat_type, msg.get("author", ""),
            msg.get("text", ""), has_link,
            msg["links"][0] if msg.get("links") else None,
            now, now
        ))

        # FTS
        rowid = conn.execute("SELECT rowid FROM messages WHERE id=?", (mid,)).fetchone()
        if rowid:
            conn.execute("""
                INSERT INTO messages_fts(rowid, text, author, chat_name)
                VALUES (?, ?, ?, ?)
            """, (rowid[0], msg.get("text", ""), msg.get("author", ""), chat_name))

        new_msgs += 1

        # Extract links
        for url in msg.get("links", []):
            url = url.rstrip(".,;:!?)>]}")
            if len(url) > 10:
                upsert_link(conn, url)
                total_links += 1

    # Create conversation snippet if we got enough messages
    if len(all_messages) >= 3:
        # Build snippet from most interesting messages (with links or longer text)
        interesting = sorted(
            all_messages.values(),
            key=lambda m: (len(m.get("links", [])) > 0, len(m.get("text", ""))),
            reverse=True
        )[:10]

        snippet = "\n".join(
            f"[{m.get('author', '?')}] {m['text'][:150]}"
            for m in interesting
        )

        participants = list(set(m.get("author", "") for m in all_messages.values() if m.get("author")))

        conn.execute("""
            INSERT INTO conversations (platform, chat_name, chat_type, snippet, participants,
                message_count, has_links, scraped_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "telegram", chat_name, chat_type, snippet[:2000],
            json.dumps(participants[:20]),
            len(all_messages),
            sum(1 for m in all_messages.values() if m.get("links")),
            now, now
        ))

    conn.commit()
    conn.close()

    stats = {
        "chat": chat_name,
        "messages": len(all_messages),
        "new": new_msgs,
        "links": total_links,
    }
    return stats


def scrape_telegram_groups(max_groups: int = 10, scroll_pages: int = 3) -> list:
    """Click into top Telegram groups and scrape messages."""
    browser, ctx = _get_browser()
    page = None
    for p in ctx.pages:
        if "telegram" in p.url:
            page = p
            break
    if not page:
        print("[telegram] No Telegram tab found")
        return []

    # Get list of group chats
    groups = page.evaluate("""() => {
        const items = document.querySelectorAll('.ListItem.Chat.chat-item-clickable.group, .ListItem.Chat.chat-item-clickable.forum');
        const result = [];
        const seen = new Set();
        items.forEach(item => {
            const nameEl = item.querySelector('h3');
            const name = nameEl ? nameEl.innerText.trim().split('\\n')[0] : '';
            if (name && !seen.has(name) && name !== 'Archived Chats') {
                seen.add(name);
                result.push(name);
            }
        });
        return result;
    }""")

    print(f"[telegram] Found {len(groups)} groups, scraping top {max_groups}")
    results = []

    for i, group_name in enumerate(groups[:max_groups]):
        print(f"  [{i+1}/{min(len(groups), max_groups)}] {group_name[:40]}...")

        # Click the group chat using JS dispatch (ripple overlay blocks normal clicks)
        clicked = page.evaluate("""(name) => {
            const items = document.querySelectorAll('.ListItem.Chat.chat-item-clickable');
            for (const item of items) {
                const h3 = item.querySelector('h3');
                if (h3 && h3.innerText.trim().startsWith(name)) {
                    const btn = item.querySelector('.ListItem-button') || item;
                    btn.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                    btn.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                    btn.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                    return true;
                }
            }
            return false;
        }""", group_name[:30])

        if not clicked:
            continue

        wait(2)
        stats = scrape_chat(page, group_name, "group", scroll_pages=scroll_pages)
        results.append(stats)
        print(f"    -> {stats['messages']} msgs, {stats['new']} new, {stats['links']} links")

        # Go back to chat list
        page.goto("https://web.telegram.org/a/", wait_until="domcontentloaded")
        wait(1)

    return results


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from db.schema import init_db
    init_db()
    scrape_telegram_groups(max_groups=5, scroll_pages=3)
