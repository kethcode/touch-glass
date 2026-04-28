"""
Central brain database schema.

All social feed data flows into a unified SQLite DB with:
- tweets (timeline, bookmarks, own, mentions — any platform)
- contacts (followers, following, mutuals — any platform)
- links (first-class citizens, always enriched)
- embeddings (semantic search vectors)
- FTS5 indexes for full-text search
- JSONB metadata columns for flexible ad-hoc fields
"""

import sqlite3
import json
import os

DB_PATH = os.environ.get("BRAIN_DB", os.path.join(os.path.dirname(__file__), "..", "brain.db"))


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(verbose: bool = True):
    conn = get_db()
    cur = conn.cursor()

    cur.executescript("""
    -- Tweets / posts from any platform
    CREATE TABLE IF NOT EXISTS tweets (
        id TEXT PRIMARY KEY,
        platform TEXT NOT NULL DEFAULT 'twitter',
        author_username TEXT,
        author_display_name TEXT,
        text TEXT,
        likes INTEGER DEFAULT 0,
        retweets INTEGER DEFAULT 0,
        replies INTEGER DEFAULT 0,
        views INTEGER DEFAULT 0,
        url TEXT,
        source TEXT,
        is_thread INTEGER DEFAULT 0,
        thread_id TEXT,
        in_reply_to TEXT,
        quoted_tweet_id TEXT,
        metadata TEXT DEFAULT '{}',
        scraped_at TEXT NOT NULL,
        created_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_tweets_author ON tweets(author_username);
    CREATE INDEX IF NOT EXISTS idx_tweets_platform ON tweets(platform);
    CREATE INDEX IF NOT EXISTS idx_tweets_source ON tweets(source);
    CREATE INDEX IF NOT EXISTS idx_tweets_created ON tweets(created_at);
    CREATE INDEX IF NOT EXISTS idx_tweets_scraped ON tweets(scraped_at);

    -- Contacts (followers, following, mutuals)
    CREATE TABLE IF NOT EXISTS contacts (
        id TEXT PRIMARY KEY,
        platform TEXT NOT NULL DEFAULT 'twitter',
        username TEXT NOT NULL,
        display_name TEXT,
        bio TEXT,
        followers_count INTEGER DEFAULT 0,
        following_count INTEGER DEFAULT 0,
        tweet_count INTEGER DEFAULT 0,
        relationship TEXT,
        profile_image TEXT,
        verified INTEGER DEFAULT 0,
        website_url TEXT,
        location TEXT,
        joined_at TEXT,
        metadata TEXT DEFAULT '{}',
        scraped_at TEXT NOT NULL,
        created_at TEXT,
        UNIQUE(platform, username)
    );
    CREATE INDEX IF NOT EXISTS idx_contacts_username ON contacts(username);
    CREATE INDEX IF NOT EXISTS idx_contacts_platform ON contacts(platform);
    CREATE INDEX IF NOT EXISTS idx_contacts_relationship ON contacts(relationship);

    -- Links — first-class citizens, always enriched
    CREATE TABLE IF NOT EXISTS links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT UNIQUE NOT NULL,
        resolved_url TEXT,
        title TEXT,
        description TEXT,
        og_image TEXT,
        content_excerpt TEXT,
        content_full TEXT,
        domain TEXT,
        platform TEXT,
        platform_id TEXT,
        content_type TEXT,
        metadata TEXT DEFAULT '{}',
        fetch_status TEXT DEFAULT 'pending',
        fetched_at TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_links_domain ON links(domain);
    CREATE INDEX IF NOT EXISTS idx_links_platform ON links(platform);
    CREATE INDEX IF NOT EXISTS idx_links_status ON links(fetch_status);

    -- Many-to-many: tweets <-> links
    CREATE TABLE IF NOT EXISTS tweet_links (
        tweet_id TEXT NOT NULL REFERENCES tweets(id),
        link_id INTEGER NOT NULL REFERENCES links(id),
        PRIMARY KEY (tweet_id, link_id)
    );

    -- Scrape log for tracking progress
    CREATE TABLE IF NOT EXISTS scrape_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task TEXT NOT NULL,
        status TEXT NOT NULL,
        items_found INTEGER DEFAULT 0,
        items_new INTEGER DEFAULT 0,
        cursor TEXT,
        error TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT
    );

    -- Embeddings for semantic search
    CREATE TABLE IF NOT EXISTS embeddings (
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        embedding BLOB NOT NULL,
        model TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (entity_type, entity_id)
    );

    -- Derived candidate events for external agents/digests
    CREATE TABLE IF NOT EXISTS detected_events (
        id TEXT PRIMARY KEY,
        topic_id TEXT NOT NULL,
        topic_name TEXT NOT NULL,
        window_start TEXT NOT NULL,
        window_end TEXT NOT NULL,
        score REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'new',
        title TEXT,
        summary TEXT,
        metadata TEXT DEFAULT '{}',
        created_at TEXT NOT NULL,
        delivered_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_detected_events_topic ON detected_events(topic_id);
    CREATE INDEX IF NOT EXISTS idx_detected_events_status ON detected_events(status);
    CREATE INDEX IF NOT EXISTS idx_detected_events_window ON detected_events(window_end);

    CREATE TABLE IF NOT EXISTS event_items (
        event_id TEXT NOT NULL REFERENCES detected_events(id) ON DELETE CASCADE,
        item_type TEXT NOT NULL,
        item_id TEXT NOT NULL,
        rank INTEGER DEFAULT 0,
        score REAL DEFAULT 0,
        metadata TEXT DEFAULT '{}',
        PRIMARY KEY (event_id, item_type, item_id)
    );

    -- Telegram messages and derived conversation snippets
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        platform TEXT NOT NULL DEFAULT 'telegram',
        chat_name TEXT,
        chat_type TEXT,
        author TEXT,
        text TEXT,
        has_link INTEGER DEFAULT 0,
        url TEXT,
        metadata TEXT DEFAULT '{}',
        scraped_at TEXT NOT NULL,
        created_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_messages_platform ON messages(platform);
    CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_name);
    CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
    CREATE INDEX IF NOT EXISTS idx_messages_scraped ON messages(scraped_at);

    CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL DEFAULT 'telegram',
        chat_name TEXT,
        chat_type TEXT,
        snippet TEXT,
        participants TEXT DEFAULT '[]',
        message_count INTEGER DEFAULT 0,
        has_links INTEGER DEFAULT 0,
        metadata TEXT DEFAULT '{}',
        scraped_at TEXT NOT NULL,
        created_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_conversations_platform ON conversations(platform);
    CREATE INDEX IF NOT EXISTS idx_conversations_chat ON conversations(chat_name);
    CREATE INDEX IF NOT EXISTS idx_conversations_scraped ON conversations(scraped_at);
    """)

    # FTS5 tables (can't use IF NOT EXISTS, so check first)
    tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    if "tweets_fts" not in tables:
        cur.execute("""
        CREATE VIRTUAL TABLE tweets_fts USING fts5(
            text, author_username, author_display_name,
            content=tweets, content_rowid=rowid
        )
        """)

    if "links_fts" not in tables:
        cur.execute("""
        CREATE VIRTUAL TABLE links_fts USING fts5(
            title, description, content_excerpt, url, domain,
            content=links, content_rowid=rowid
        )
        """)

    if "contacts_fts" not in tables:
        cur.execute("""
        CREATE VIRTUAL TABLE contacts_fts USING fts5(
            username, display_name, bio,
            content=contacts, content_rowid=rowid
        )
        """)

    if "messages_fts" not in tables:
        cur.execute("""
        CREATE VIRTUAL TABLE messages_fts USING fts5(
            text, author, chat_name,
            content=messages, content_rowid=rowid
        )
        """)

    conn.commit()
    conn.close()
    if verbose:
        print(f"Database initialized at {DB_PATH}")


# --- Helper functions for upserting with FTS sync ---

def upsert_tweet(conn, tweet: dict) -> bool:
    """Insert or update a tweet. Returns True if new."""
    existing = conn.execute("SELECT id FROM tweets WHERE id = ?", (tweet["id"],)).fetchone()
    if existing:
        # Update engagement stats
        conn.execute("""
            UPDATE tweets SET likes=?, retweets=?, replies=?, views=?, metadata=?, scraped_at=?
            WHERE id=?
        """, (
            tweet.get("likes", 0), tweet.get("retweets", 0),
            tweet.get("replies", 0), tweet.get("views", 0),
            json.dumps(tweet.get("metadata", {})),
            tweet["scraped_at"], tweet["id"]
        ))
        return False
    else:
        conn.execute("""
            INSERT INTO tweets (id, platform, author_username, author_display_name, text,
                likes, retweets, replies, views, url, source, is_thread, thread_id,
                in_reply_to, quoted_tweet_id, metadata, scraped_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            tweet["id"], tweet.get("platform", "twitter"),
            tweet.get("author_username"), tweet.get("author_display_name"),
            tweet.get("text"), tweet.get("likes", 0), tweet.get("retweets", 0),
            tweet.get("replies", 0), tweet.get("views", 0),
            tweet.get("url"), tweet.get("source"),
            tweet.get("is_thread", 0), tweet.get("thread_id"),
            tweet.get("in_reply_to"), tweet.get("quoted_tweet_id"),
            json.dumps(tweet.get("metadata", {})),
            tweet["scraped_at"], tweet.get("created_at")
        ))
        # Sync FTS
        rowid = conn.execute("SELECT rowid FROM tweets WHERE id=?", (tweet["id"],)).fetchone()[0]
        conn.execute("""
            INSERT INTO tweets_fts(rowid, text, author_username, author_display_name)
            VALUES (?, ?, ?, ?)
        """, (rowid, tweet.get("text", ""), tweet.get("author_username", ""),
              tweet.get("author_display_name", "")))
        return True


def upsert_contact(conn, contact: dict) -> bool:
    """Insert or update a contact. Returns True if new."""
    existing = conn.execute(
        "SELECT id FROM contacts WHERE platform=? AND username=?",
        (contact.get("platform", "twitter"), contact["username"])
    ).fetchone()
    if existing:
        conn.execute("""
            UPDATE contacts SET display_name=?, bio=?, followers_count=?, following_count=?,
                tweet_count=?, relationship=?, profile_image=?, verified=?, website_url=?,
                location=?, metadata=?, scraped_at=?
            WHERE platform=? AND username=?
        """, (
            contact.get("display_name"), contact.get("bio"),
            contact.get("followers_count", 0), contact.get("following_count", 0),
            contact.get("tweet_count", 0), contact.get("relationship"),
            contact.get("profile_image"), contact.get("verified", 0),
            contact.get("website_url"), contact.get("location"),
            json.dumps(contact.get("metadata", {})), contact["scraped_at"],
            contact.get("platform", "twitter"), contact["username"]
        ))
        return False
    else:
        cid = f"{contact.get('platform', 'twitter')}:{contact['username']}"
        conn.execute("""
            INSERT INTO contacts (id, platform, username, display_name, bio,
                followers_count, following_count, tweet_count, relationship,
                profile_image, verified, website_url, location, joined_at,
                metadata, scraped_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            cid, contact.get("platform", "twitter"), contact["username"],
            contact.get("display_name"), contact.get("bio"),
            contact.get("followers_count", 0), contact.get("following_count", 0),
            contact.get("tweet_count", 0), contact.get("relationship"),
            contact.get("profile_image"), contact.get("verified", 0),
            contact.get("website_url"), contact.get("location"),
            contact.get("joined_at"), json.dumps(contact.get("metadata", {})),
            contact["scraped_at"], contact.get("created_at")
        ))
        rowid = conn.execute("SELECT rowid FROM contacts WHERE id=?", (cid,)).fetchone()[0]
        conn.execute("""
            INSERT INTO contacts_fts(rowid, username, display_name, bio)
            VALUES (?, ?, ?, ?)
        """, (rowid, contact["username"], contact.get("display_name", ""),
              contact.get("bio", "")))
        return True


def upsert_link(conn, url: str, **kwargs) -> int:
    """Insert or update a link. Returns link ID."""
    existing = conn.execute("SELECT id FROM links WHERE url=?", (url,)).fetchone()
    if existing:
        if kwargs:
            sets = ", ".join(f"{k}=?" for k in kwargs)
            conn.execute(f"UPDATE links SET {sets} WHERE url=?", (*kwargs.values(), url))
        return existing[0]
    else:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        # Detect platform from URL
        platform = None
        platform_id = None
        if "twitter.com" in domain or "x.com" in domain:
            platform = "twitter"
            import re
            m = re.search(r"/status/(\d+)", url)
            if m:
                platform_id = m.group(1)
        elif "linkedin.com" in domain:
            platform = "linkedin"
        elif "youtube.com" in domain or "youtu.be" in domain:
            platform = "youtube"

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO links (url, domain, platform, platform_id, created_at,
                resolved_url, title, description, og_image, content_excerpt,
                content_type, metadata, fetch_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            url, domain, platform, platform_id, now,
            kwargs.get("resolved_url"), kwargs.get("title"),
            kwargs.get("description"), kwargs.get("og_image"),
            kwargs.get("content_excerpt"), kwargs.get("content_type"),
            json.dumps(kwargs.get("metadata", {})),
            kwargs.get("fetch_status", "pending")
        ))
        link_id = conn.execute("SELECT id FROM links WHERE url=?", (url,)).fetchone()[0]
        rowid = conn.execute("SELECT rowid FROM links WHERE id=?", (link_id,)).fetchone()[0]
        conn.execute("""
            INSERT INTO links_fts(rowid, title, description, content_excerpt, url, domain)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (rowid, kwargs.get("title", ""), kwargs.get("description", ""),
              kwargs.get("content_excerpt", ""), url, domain))
        return link_id


def link_tweet_url(conn, tweet_id: str, link_id: int):
    """Create tweet <-> link association."""
    conn.execute(
        "INSERT OR IGNORE INTO tweet_links (tweet_id, link_id) VALUES (?, ?)",
        (tweet_id, link_id)
    )


if __name__ == "__main__":
    init_db()
