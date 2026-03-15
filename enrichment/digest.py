"""
Daily morning digest — Top 5 tweets + Top 5 links.

Curated for: AI, stablecoins, crypto launches, GitHub repos,
new capabilities, lab announcements.
"""

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta

from db.schema import get_db

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
DIGEST_TO = os.environ.get("DIGEST_EMAIL", "")
DIGEST_FROM = os.environ.get("DIGEST_FROM", "Touch Glass <onboarding@resend.dev>")

# --- Scoring weights ---

# Primary interest: AI / agents / dev tools
AI_TERMS = {
    # Core AI
    "claude": 4, "anthropic": 5, "openai": 4, "gpt": 3, "gemini": 3,
    "llm": 3, "llama": 3, "mistral": 3, "deepseek": 3, "qwen": 3,
    "artificial intelligence": 3, "machine learning": 2, "deep learning": 2,
    # Agentic
    "agent": 3, "agentic": 4, "mcp": 4, "tool use": 3, "function calling": 3,
    "claude code": 6, "cursor": 3, "copilot": 3, "replit": 2,
    "autonomous": 2, "orchestrat": 2, "multi-agent": 4,
    # Technical
    "transformer": 2, "reasoning": 3, "benchmark": 2, "context window": 3,
    "fine-tune": 2, "fine-tuning": 2, "rlhf": 2, "alignment": 2,
    "embedding": 2, "rag": 2, "retrieval": 2, "inference": 2,
    "multimodal": 3, "vision model": 3, "voice model": 3,
    "open source": 2, "open-source": 2, "weights": 2,
    # Products / launches
    "launch": 3, "released": 3, "announcing": 4, "just shipped": 4,
    "now available": 3, "introducing": 4, "new model": 4,
    "api": 2, "sdk": 2, "developer": 2,
}

# Secondary: crypto / stablecoins
CRYPTO_TERMS = {
    "stablecoin": 5, "usdc": 3, "usdt": 3, "dai": 2,
    "circle": 3, "tether": 2, "bridge": 2,
    "bitcoin": 2, "ethereum": 2, "solana": 2,
    "defi": 2, "onchain": 2, "on-chain": 2,
    "tokeniz": 3, "rwa": 3, "real world asset": 3,
    "cbdc": 3, "digital currency": 2,
    "regulation": 2, "sec ": 2, "mica": 2,
}

# High-value content signals
QUALITY_SIGNALS = {
    "github.com": 6, "github": 4, "open-source": 3,
    "research paper": 4, "arxiv": 4, "paper:": 3,
    "thread": 2, "deep dive": 3, "breakdown": 2,
    "benchmark": 3, "comparison": 2, "vs ": 1,
    "tutorial": 2, "how to": 1, "guide": 1,
    "billion": 2, "million": 2, "raised": 2, "funding": 2,
    "acquired": 3, "acquisition": 3,
}

# Deprioritize noise
NOISE_PENALTY = {
    "giveaway": -5, "airdrop": -3, "drop your": -4,
    "follow + rt": -5, "like + comment": -5, "must follow": -5,
    "dm me": -3, "send me": -2, "comment below": -3,
    "not financial advice": -2, "nfa": -1, "dyor": -1,
    "gm ": -2, "gn ": -2,
}


def score_tweet(tweet: dict) -> tuple[float, list[str]]:
    """Score a tweet. Returns (score, [reason tags])."""
    text = (tweet.get("text") or "").lower()
    tags = []
    score = 0.0

    if len(text) < 25:
        return 0, []

    # AI terms
    for term, weight in AI_TERMS.items():
        if term in text:
            score += weight
            if weight >= 3:
                tags.append(term)

    # Crypto terms
    for term, weight in CRYPTO_TERMS.items():
        if term in text:
            score += weight
            if weight >= 3:
                tags.append(term)

    # Quality signals
    for term, weight in QUALITY_SIGNALS.items():
        if term in text:
            score += weight
            if weight >= 3:
                tags.append(term)

    # Noise penalty
    for term, weight in NOISE_PENALTY.items():
        if term in text:
            score += weight

    # Engagement bonus (logarithmic — viral matters but doesn't dominate)
    likes = tweet.get("likes", 0) or 0
    if likes > 5000: score += 5
    elif likes > 1000: score += 3
    elif likes > 100: score += 1.5
    elif likes > 20: score += 0.5

    retweets = tweet.get("retweets", 0) or 0
    if retweets > 500: score += 3
    elif retweets > 100: score += 1.5

    # Boost bookmarks (you saved it = signal)
    if tweet.get("source") == "bookmarks":
        score *= 1.4

    # Boost tweets with links (more substantive)
    if tweet.get("links_count", 0) > 0:
        score += 2

    # Boost longer, more substantive tweets
    if len(text) > 200:
        score += 1

    return round(score, 1), tags[:5]


def score_link(link: dict) -> tuple[float, list[str]]:
    """Score a link for the digest."""
    url = (link.get("url") or "").lower()
    title = (link.get("title") or "").lower()
    desc = (link.get("description") or "").lower()
    domain = (link.get("domain") or "").lower()
    full = f"{title} {desc} {url}"
    tags = []
    score = 0.0

    # GitHub repos = gold
    if "github.com" in url:
        score += 8
        tags.append("github")

    # Domain quality
    high_value_domains = {
        "arxiv.org": 6, "huggingface.co": 5, "openai.com": 5,
        "anthropic.com": 6, "blog.google": 4, "ai.meta.com": 4,
        "stability.ai": 3, "mistral.ai": 4, "deepmind.com": 4,
        "techcrunch.com": 3, "theverge.com": 2, "wired.com": 2,
        "a]6z.com": 3, "sequoiacap.com": 3,
    }
    for d, w in high_value_domains.items():
        if d in domain:
            score += w
            tags.append(d.split(".")[0])

    # Content scoring
    all_terms = {**AI_TERMS, **CRYPTO_TERMS, **QUALITY_SIGNALS}
    for term, weight in all_terms.items():
        if term in full:
            score += weight * 0.5  # Half weight vs tweets (less context)
            if weight >= 4:
                tags.append(term)

    return round(score, 1), tags[:5]


def generate_digest() -> dict:
    """Generate top 5 tweets + top 5 links from last 24h."""
    conn = get_db()
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    # --- Top 5 Tweets ---
    tweets = conn.execute("""
        SELECT t.*, COUNT(tl.link_id) as links_count
        FROM tweets t
        LEFT JOIN tweet_links tl ON t.id = tl.tweet_id
        WHERE t.scraped_at > ?
        GROUP BY t.id
    """, (since,)).fetchall()

    scored_tweets = []
    for t in tweets:
        td = dict(t)
        s, tags = score_tweet(td)
        if s >= 5:
            td["score"] = s
            td["tags"] = tags
            scored_tweets.append(td)

    scored_tweets.sort(key=lambda x: x["score"], reverse=True)

    # Deduplicate by author (max 1 per author in top 5)
    seen_authors = set()
    top_tweets = []
    for t in scored_tweets:
        author = t.get("author_username", "")
        if author in seen_authors:
            continue
        seen_authors.add(author)
        # Fetch associated links
        links = conn.execute("""
            SELECT l.url, l.title, l.description, l.domain
            FROM links l JOIN tweet_links tl ON l.id = tl.link_id
            WHERE tl.tweet_id = ?
        """, (t["id"],)).fetchall()
        t["enriched_links"] = [dict(l) for l in links]
        top_tweets.append(t)
        if len(top_tweets) >= 5:
            break

    # --- Top 5 Links ---
    links = conn.execute("""
        SELECT * FROM links
        WHERE fetch_status = 'done' AND created_at > ?
        AND title IS NOT NULL AND LENGTH(title) > 5
    """, (since,)).fetchall()

    scored_links = []
    for l in links:
        ld = dict(l)
        s, tags = score_link(ld)
        if s >= 3:
            ld["score"] = s
            ld["tags"] = tags
            scored_links.append(ld)

    scored_links.sort(key=lambda x: x["score"], reverse=True)

    # Deduplicate by domain (max 2 per domain)
    domain_count = {}
    top_links = []
    for l in scored_links:
        d = l.get("domain", "")
        domain_count[d] = domain_count.get(d, 0) + 1
        if domain_count[d] > 2:
            continue
        top_links.append(l)
        if len(top_links) >= 5:
            break

    conn.close()

    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_scraped": len(tweets),
        "top_tweets": top_tweets,
        "top_links": top_links,
    }


def format_digest_html(digest: dict) -> str:
    date = digest["date"]

    # --- Tweets section ---
    tweets_html = ""
    for i, t in enumerate(digest["top_tweets"], 1):
        author = t.get("author_username") or t.get("author_display_name") or "?"
        text = (t.get("text") or "")[:400]
        likes = t.get("likes", 0) or 0
        retweets = t.get("retweets", 0) or 0
        url = t.get("url", "")
        tags = t.get("tags", [])
        source = t.get("source", "")
        platform = t.get("platform", "twitter")

        tags_html = " ".join(
            f'<span style="background:#1a1a2e;color:#58a6ff;padding:1px 6px;border-radius:3px;font-size:10px;margin-right:4px;">{tag}</span>'
            for tag in tags
        )

        links_html = ""
        for link in t.get("enriched_links", []):
            title = link.get("title") or link.get("url", "")
            links_html += f'<div style="margin:4px 0;font-size:12px;">&#128279; <a href="{link["url"]}" style="color:#58a6ff;text-decoration:none;">{title[:70]}</a> <span style="color:#6e7681;">({link.get("domain", "")})</span></div>'

        author_url = f"https://x.com/{author}" if platform == "twitter" else "#"

        tweets_html += f"""
        <div style="border:1px solid #30363d;border-radius:8px;padding:14px;margin:8px 0;background:#161b22;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
                <a href="{author_url}" style="font-weight:bold;color:#58a6ff;text-decoration:none;font-size:14px;">@{author}</a>
                <span style="color:#6e7681;font-size:11px;">{source} &middot; {platform}</span>
            </div>
            <div style="color:#e6edf3;line-height:1.5;font-size:14px;margin-bottom:8px;">{text}</div>
            {links_html}
            <div style="margin-top:6px;">{tags_html}</div>
            <div style="color:#6e7681;font-size:11px;margin-top:6px;">
                &#10084; {likes:,} &middot; &#8634; {retweets:,}
                {f' &middot; <a href="{url}" style="color:#58a6ff;text-decoration:none;">View tweet &#8599;</a>' if url else ''}
            </div>
        </div>"""

    # --- Links section ---
    links_html = ""
    for i, l in enumerate(digest["top_links"], 1):
        title = l.get("title") or l.get("url", "")
        desc = (l.get("description") or "")[:200]
        domain = l.get("domain", "")
        url = l.get("url", "")
        tags = l.get("tags", [])

        tags_html = " ".join(
            f'<span style="background:#1a2e1a;color:#3fb950;padding:1px 6px;border-radius:3px;font-size:10px;margin-right:4px;">{tag}</span>'
            for tag in tags
        )

        links_html += f"""
        <div style="border:1px solid #30363d;border-radius:8px;padding:14px;margin:8px 0;background:#161b22;">
            <a href="{url}" style="font-weight:bold;color:#58a6ff;text-decoration:none;font-size:14px;display:block;margin-bottom:4px;">{title[:80]}</a>
            <div style="color:#8b949e;font-size:13px;line-height:1.4;margin-bottom:6px;">{desc}</div>
            <div style="margin-top:4px;">{tags_html}</div>
            <div style="color:#6e7681;font-size:11px;margin-top:4px;">{domain}</div>
        </div>"""

    no_tweets = '<div style="color:#6e7681;padding:20px;text-align:center;">No high-signal tweets in the last 24h</div>' if not tweets_html else ""
    no_links = '<div style="color:#6e7681;padding:20px;text-align:center;">No high-signal links in the last 24h</div>' if not links_html else ""

    return f"""
    <html>
    <body style="background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:640px;margin:0 auto;padding:20px;">
        <div style="border-bottom:1px solid #30363d;padding-bottom:12px;margin-bottom:20px;">
            <h1 style="color:#e6edf3;font-size:20px;margin:0;">Touch Glass &mdash; {date}</h1>
            <div style="color:#6e7681;font-size:13px;margin-top:4px;">
                {len(digest['top_tweets'])} tweets &middot; {len(digest['top_links'])} links &middot; {digest['total_scraped']:,} scanned
            </div>
        </div>

        <h2 style="color:#e6edf3;font-size:15px;margin:16px 0 8px;text-transform:uppercase;letter-spacing:1px;">Top 5 Tweets</h2>
        {tweets_html or no_tweets}

        <h2 style="color:#e6edf3;font-size:15px;margin:24px 0 8px;text-transform:uppercase;letter-spacing:1px;">Top 5 Links</h2>
        {links_html or no_links}

        <div style="text-align:center;color:#6e7681;margin-top:30px;padding-top:16px;border-top:1px solid #21262d;font-size:11px;">
            Touch Glass &middot; AI &middot; Stablecoins &middot; Crypto &middot; Launches
        </div>
    </body>
    </html>
    """


def send_digest():
    """Generate and send the daily digest."""
    digest = generate_digest()

    if not digest["top_tweets"] and not digest["top_links"]:
        print("[digest] Nothing to send")
        return digest

    # Console output
    print(f"[digest] {len(digest['top_tweets'])} tweets, {len(digest['top_links'])} links from {digest['total_scraped']:,} scanned")
    for i, t in enumerate(digest["top_tweets"], 1):
        print(f"  T{i}. [{t['score']}] @{t.get('author_username','?')}: {(t.get('text') or '')[:70]}")
    for i, l in enumerate(digest["top_links"], 1):
        print(f"  L{i}. [{l['score']}] {l.get('title','?')[:50]} ({l.get('domain','')})")

    if not RESEND_API_KEY or not DIGEST_TO:
        print("[digest] No RESEND_API_KEY or DIGEST_EMAIL set, skipping email")
        return digest

    html = format_digest_html(digest)
    date = digest["date"]
    n_tweets = len(digest["top_tweets"])
    n_links = len(digest["top_links"])

    payload = json.dumps({
        "from": DIGEST_FROM,
        "to": [DIGEST_TO],
        "subject": f"Touch Glass — {date} — {n_tweets} tweets, {n_links} links",
        "html": html,
    })

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write(payload)
        payload_file = f.name

    try:
        result = subprocess.run([
            "curl", "-s", "-X", "POST",
            "https://api.resend.com/emails",
            "-H", f"Authorization: Bearer {RESEND_API_KEY}",
            "-H", "Content-Type: application/json",
            "-d", f"@{payload_file}",
        ], capture_output=True, text=True, timeout=15)
        os.unlink(payload_file)

        if result.returncode == 0 and '"id"' in result.stdout:
            print(f"[digest] Email sent: {result.stdout.strip()}")
        else:
            print(f"[digest] Email response: {result.stdout.strip()}")
        return digest
    except Exception as e:
        print(f"[digest] Email failed: {e}")
        try:
            os.unlink(payload_file)
        except Exception:
            pass
        return digest


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from db.schema import init_db
    init_db()
    send_digest()
