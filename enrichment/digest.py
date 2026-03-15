"""
Daily email digest — top 20 AI-useful items from timeline & bookmarks.

Uses Resend API (borrowed from PopPay setup).
Sends to  every morning.
"""

import json
import os
import urllib.request
from datetime import datetime, timezone, timedelta

from db.schema import get_db

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
DIGEST_TO = os.environ.get("DIGEST_EMAIL", "")
DIGEST_FROM = os.environ.get("DIGEST_FROM", "Touch Glass <onboarding@resend.dev>")

# AI-related keywords for scoring
AI_KEYWORDS = [
    "ai", "artificial intelligence", "machine learning", "llm", "gpt",
    "claude", "anthropic", "openai", "gemini", "transformer", "neural",
    "deep learning", "ml", "nlp", "computer vision", "diffusion",
    "agent", "rag", "embedding", "fine-tune", "foundation model",
    "reasoning", "benchmark", "training", "inference", "token",
    "prompt", "context window", "multimodal", "vision model",
    "model", "dataset", "parameter", "weights", "alignment",
    "rlhf", "mcp", "tool use", "function calling", "agentic",
    "autonomous", "code generation", "copilot", "cursor", "replit",
    "hugging face", "mistral", "llama", "open source ai",
    "robotics", "automation", "api", "sdk", "developer tools",
    "startup", "funding", "launch", "product", "saas",
]


def score_tweet(tweet: dict) -> float:
    """Score a tweet for AI-relevance. Higher = more relevant."""
    text = (tweet.get("text") or "").lower()
    author = (tweet.get("author_username") or "").lower()

    score = 0.0

    # Keyword matches
    for kw in AI_KEYWORDS:
        if kw in text:
            score += 2.0
            if len(kw) > 5:  # Longer keywords are more specific
                score += 1.0

    # Engagement signals
    likes = tweet.get("likes", 0) or 0
    views = tweet.get("views", 0) or 0
    retweets = tweet.get("retweets", 0) or 0

    if likes > 1000:
        score += 3.0
    elif likes > 100:
        score += 1.5
    elif likes > 10:
        score += 0.5

    if views > 100000:
        score += 2.0
    elif views > 10000:
        score += 1.0

    if retweets > 100:
        score += 2.0

    # Has links (usually more substantive)
    if tweet.get("links_count", 0) > 0:
        score += 1.0

    # Penalize very short tweets
    if len(text) < 30:
        score *= 0.5

    # Boost bookmarks (user explicitly saved these)
    if tweet.get("source") == "bookmarks":
        score *= 1.5

    return score


def generate_digest() -> dict:
    """Generate a digest of top AI-relevant items from last 24h."""
    conn = get_db()
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    # Get recent tweets
    tweets = conn.execute("""
        SELECT t.*, COUNT(tl.link_id) as links_count
        FROM tweets t
        LEFT JOIN tweet_links tl ON t.id = tl.tweet_id
        WHERE t.scraped_at > ? AND t.platform = 'twitter'
        GROUP BY t.id
        ORDER BY t.scraped_at DESC
    """, (since,)).fetchall()

    # Score and rank
    scored = []
    for t in tweets:
        td = dict(t)
        td["ai_score"] = score_tweet(td)
        if td["ai_score"] > 2.0:  # Minimum relevance threshold
            scored.append(td)

    scored.sort(key=lambda x: x["ai_score"], reverse=True)
    top_20 = scored[:20]

    # Get associated links
    for item in top_20:
        links = conn.execute("""
            SELECT l.url, l.title, l.description, l.domain
            FROM links l
            JOIN tweet_links tl ON l.id = tl.link_id
            WHERE tl.tweet_id = ?
        """, (item["id"],)).fetchall()
        item["enriched_links"] = [dict(l) for l in links]

    # Stats
    total_tweets = len(tweets)
    ai_relevant = len(scored)

    conn.close()

    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_scraped": total_tweets,
        "ai_relevant": ai_relevant,
        "top_20": top_20,
    }


def format_digest_html(digest: dict) -> str:
    """Format digest as HTML email."""
    date = digest["date"]
    items_html = ""

    for i, item in enumerate(digest["top_20"], 1):
        author = item.get("author_username", "unknown")
        text = (item.get("text") or "")[:300]
        likes = item.get("likes", 0) or 0
        views = item.get("views", 0) or 0
        url = item.get("url", "")
        source = item.get("source", "")
        score = item.get("ai_score", 0)

        links_html = ""
        for link in item.get("enriched_links", []):
            title = link.get("title") or link.get("url", "")
            links_html += f'<div style="margin:4px 0;font-size:13px;">🔗 <a href="{link["url"]}" style="color:#58a6ff;">{title[:80]}</a> <span style="color:#8b949e;">({link.get("domain", "")})</span></div>'

        source_badge = f'<span style="background:#1a1a2e;color:#58a6ff;padding:2px 8px;border-radius:4px;font-size:11px;">{source}</span>'

        items_html += f"""
        <div style="border:1px solid #30363d;border-radius:8px;padding:16px;margin:12px 0;background:#161b22;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <span style="font-weight:bold;color:#58a6ff;">#{i} @{author}</span>
                {source_badge}
            </div>
            <div style="color:#c9d1d9;line-height:1.5;margin-bottom:8px;">{text}</div>
            {links_html}
            <div style="color:#8b949e;font-size:12px;margin-top:8px;">
                ❤️ {likes:,} | 👁️ {views:,} | Score: {score:.1f}
                {f' | <a href="{url}" style="color:#58a6ff;">View</a>' if url else ''}
            </div>
        </div>
        """

    return f"""
    <html>
    <body style="background:#0d1117;color:#c9d1d9;font-family:-apple-system,sans-serif;max-width:700px;margin:0 auto;padding:20px;">
        <h1 style="color:#58a6ff;border-bottom:1px solid #30363d;padding-bottom:12px;">
            🧠 Brain Digest — {date}
        </h1>
        <div style="color:#8b949e;margin-bottom:20px;">
            Scraped {digest['total_scraped']} items | {digest['ai_relevant']} AI-relevant | Top 20 below
        </div>
        {items_html}
        <div style="text-align:center;color:#8b949e;margin-top:30px;padding-top:20px;border-top:1px solid #30363d;font-size:12px;">
            Generated by Brain Monitor 🧠
        </div>
    </body>
    </html>
    """


def send_digest():
    """Generate and send the daily digest email."""
    if not RESEND_API_KEY:
        print("[digest] No RESEND_API_KEY set, printing to console")
        digest = generate_digest()
        print(f"Digest: {digest['total_scraped']} items, {digest['ai_relevant']} AI-relevant")
        for i, item in enumerate(digest["top_20"], 1):
            print(f"  {i}. @{item.get('author_username', '?')}: {(item.get('text') or '')[:80]}")
        return digest

    digest = generate_digest()
    if not digest["top_20"]:
        print("[digest] No AI-relevant items to send")
        return digest

    html = format_digest_html(digest)
    date = digest["date"]

    payload = json.dumps({
        "from": DIGEST_FROM,
        "to": [DIGEST_TO],
        "subject": f"Brain Digest — {date} — {len(digest['top_20'])} AI items",
        "html": html,
    })

    # Use curl to avoid Cloudflare blocking Python urllib
    import subprocess
    import tempfile
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
