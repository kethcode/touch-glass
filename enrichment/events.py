"""
Detect candidate events from the recent social feed.

This module is intentionally deterministic: it turns recent scraped items into
small event bundles that a commentary agent can interpret and deliver.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from db.schema import get_db, init_db


DEFAULT_CONFIG = {
    "defaults": {
        "window_minutes": 120,
        "min_items": 2,
        "min_score": 8,
        "max_items": 500,
        "official_source_score": 4,
        "security_advisory_score": 5,
        "same_author_pileon_penalty": 3,
        "meme_only_cluster_penalty": 2,
    },
    "topics": [
        {
            "id": "ai_agents",
            "name": "AI agents and dev tools",
            "priority": 5,
            "keywords": [
                "agent", "agents", "agentic", "mcp", "tool use", "function calling",
                "claude code", "cursor", "copilot", "codegen", "coding agent",
                "autonomous", "workflow automation",
            ],
            "domains": ["github.com", "anthropic.com", "openai.com", "huggingface.co"],
            "official_accounts": ["anthropicai", "openai", "github", "huggingface"],
            "official_domains": ["anthropic.com", "openai.com", "github.blog", "huggingface.co"],
        },
        {
            "id": "ai_models",
            "name": "AI model releases",
            "priority": 4,
            "keywords": [
                "model release", "new model", "benchmark", "eval", "weights",
                "open source model", "qwen", "llama", "mistral", "deepseek",
                "gemini", "gpt", "claude", "inference",
            ],
            "domains": ["huggingface.co", "arxiv.org", "openai.com", "anthropic.com"],
            "official_accounts": ["qwenlm", "openai", "anthropicai", "mistralai", "deepseek_ai"],
            "official_domains": ["huggingface.co", "openai.com", "anthropic.com", "mistral.ai"],
        },
        {
            "id": "stablecoins",
            "name": "Stablecoins and payments",
            "priority": 4,
            "keywords": [
                "stablecoin", "stablecoins", "usdc", "usdt", "circle", "tether",
                "payments", "payment rail", "tokenized deposits", "rwa",
            ],
            "domains": ["circle.com", "tether.to"],
            "official_accounts": ["circle", "tether_to"],
            "official_domains": ["circle.com", "tether.to"],
        },
        {
            "id": "crypto_launches",
            "name": "Crypto launches and infra",
            "priority": 3,
            "keywords": [
                "mainnet", "testnet", "airdrop", "launch", "protocol", "defi",
                "ethereum", "solana", "bitcoin", "onchain", "rollup", "zk",
            ],
            "domains": ["github.com"],
            "official_domains": ["github.com"],
        },
    ],
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def load_config(path: str | None = None) -> dict:
    path = path or os.environ.get("TOUCH_GLASS_TOPICS_CONFIG", "topics.json")
    if not path or not os.path.exists(path):
        return DEFAULT_CONFIG
    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)
    config.setdefault("defaults", {})
    config.setdefault("topics", [])
    return config


def _clean_text(text: str | None, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _domain(url: str | None) -> str:
    if not url:
        return ""
    domain = urlparse(url).netloc.lower()
    return domain[4:] if domain.startswith("www.") else domain


def fetch_recent_tweets(conn, since: datetime, max_items: int = 500) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM tweets
        WHERE platform='twitter' AND scraped_at >= ?
        ORDER BY scraped_at DESC
        LIMIT ?
        """,
        (iso(since), max_items),
    ).fetchall()

    items = []
    for row in rows:
        item = dict(row)
        links = conn.execute(
            """
            SELECT l.* FROM links l
            JOIN tweet_links tl ON tl.link_id = l.id
            WHERE tl.tweet_id = ?
            """,
            (item["id"],),
        ).fetchall()
        item["links"] = [dict(link) for link in links]
        items.append(item)
    return items


def _topic_terms(topic: dict, key: str) -> list[str]:
    return [str(term).strip().lower() for term in topic.get(key, []) if str(term).strip()]


def score_item_for_topic(item: dict, topic: dict) -> tuple[float, dict]:
    text_parts = [
        item.get("text") or "",
        item.get("author_username") or "",
        item.get("author_display_name") or "",
    ]
    link_domains = []
    for link in item.get("links", []):
        text_parts.extend([
            link.get("title") or "",
            link.get("description") or "",
            link.get("domain") or "",
            link.get("url") or "",
        ])
        if link.get("domain"):
            link_domains.append(str(link["domain"]).lower())
        elif link.get("url"):
            link_domains.append(_domain(link["url"]))

    haystack = " ".join(text_parts).lower()
    keywords = _topic_terms(topic, "keywords")
    domains = _topic_terms(topic, "domains")
    accounts = [term.lstrip("@") for term in _topic_terms(topic, "accounts")]
    official_accounts = [term.lstrip("@") for term in _topic_terms(topic, "official_accounts")]
    official_domains = _topic_terms(topic, "official_domains")
    security_terms = _topic_terms(topic, "security_terms")
    meme_terms = _topic_terms(topic, "meme_terms")

    matched_keywords = [term for term in keywords if term in haystack]
    matched_domains = [
        domain for domain in domains
        if any(domain in link_domain for link_domain in link_domains)
    ]
    author = (item.get("author_username") or "").lower().lstrip("@")
    matched_accounts = [account for account in accounts if account == author]
    matched_official_accounts = [account for account in official_accounts if account == author]
    matched_official_domains = [
        domain for domain in official_domains
        if any(domain in link_domain for link_domain in link_domains)
    ]
    matched_security_terms = [term for term in security_terms if term in haystack]
    matched_meme_terms = [term for term in meme_terms if term in haystack]

    if not matched_keywords and not matched_domains and not matched_accounts:
        return 0, {}

    score = 0.0
    score += len(matched_keywords) * 1.4
    score += len(matched_domains) * 2.0
    score += len(matched_accounts) * 4.0

    likes = item.get("likes") or 0
    retweets = item.get("retweets") or 0
    replies = item.get("replies") or 0
    score += min(likes / 250, 4.0)
    score += min(retweets / 100, 3.0)
    score += min(replies / 100, 1.5)
    if item.get("source") == "bookmarks":
        score += 1.0
    if item.get("links"):
        score += 0.75

    metadata = {
        "matched_keywords": matched_keywords,
        "matched_domains": matched_domains,
        "matched_accounts": matched_accounts,
        "matched_official_accounts": matched_official_accounts,
        "matched_official_domains": matched_official_domains,
        "matched_security_terms": matched_security_terms,
        "matched_meme_terms": matched_meme_terms,
        "link_domains": sorted(set(link_domains)),
    }
    return round(score, 2), metadata


def _build_event_id(topic_id: str, items: list[dict]) -> str:
    top_ids = ",".join(item["id"] for item in items[:8])
    seed = f"{topic_id}|{top_ids}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"{topic_id}:{digest}"


def _build_summary(topic_name: str, items: list[dict], metadata: dict) -> str:
    authors = metadata.get("top_authors", [])
    terms = metadata.get("top_terms", [])
    parts = [
        f"{len(items)} matching items",
        f"{metadata.get('unique_authors', 0)} authors",
    ]
    if authors:
        parts.append("authors: " + ", ".join(f"@{a}" for a in authors[:4] if a))
    if terms:
        parts.append("terms: " + ", ".join(terms[:8]))
    signals = []
    if metadata.get("official_source_seen"):
        signals.append("official source")
    if metadata.get("security_advisory_seen"):
        signals.append("security advisory")
    if metadata.get("same_author_pileon"):
        signals.append("single-author pileon")
    if metadata.get("meme_only_cluster"):
        signals.append("meme-only cluster")
    if signals:
        parts.append("signals: " + ", ".join(signals))
    return f"{topic_name}: " + "; ".join(parts)


def score_event(matches: list[dict], topic: dict, defaults: dict, author_counts: Counter, domain_counts: Counter) -> tuple[float, dict]:
    unique_authors = len(author_counts)
    unique_link_domains = len(domain_counts)
    official_source_seen = any(
        item.get("event_item_metadata", {}).get("matched_official_accounts")
        or item.get("event_item_metadata", {}).get("matched_official_domains")
        for item in matches
    )
    security_advisory_seen = any(
        item.get("event_item_metadata", {}).get("matched_security_terms")
        for item in matches
    )
    meme_hits = sum(
        1 for item in matches
        if item.get("event_item_metadata", {}).get("matched_meme_terms")
    )
    same_author_pileon = bool(matches) and unique_authors == 1 and len(matches) >= 3
    meme_only_cluster = bool(matches) and meme_hits == len(matches) and not official_source_seen and not security_advisory_seen

    score = 0.0
    score += 1.0 * len(matches)
    score += 2.0 * unique_authors
    score += 2.0 * unique_link_domains
    if official_source_seen:
        score += float(topic.get("official_source_score", defaults.get("official_source_score", 4)))
    if security_advisory_seen:
        score += float(topic.get("security_advisory_score", defaults.get("security_advisory_score", 5)))
    if same_author_pileon:
        score -= float(topic.get("same_author_pileon_penalty", defaults.get("same_author_pileon_penalty", 3)))
    if meme_only_cluster:
        score -= float(topic.get("meme_only_cluster_penalty", defaults.get("meme_only_cluster_penalty", 2)))

    # Keep strong individual posts from being invisible, but do not let engagement
    # dominate source diversity.
    score += min(sum(item["event_item_score"] for item in matches[:10]) * 0.15, 4.0)

    components = {
        "matching_posts": len(matches),
        "unique_authors": unique_authors,
        "unique_link_domains": unique_link_domains,
        "official_source_seen": official_source_seen,
        "security_advisory_seen": security_advisory_seen,
        "same_author_pileon": same_author_pileon,
        "meme_only_cluster": meme_only_cluster,
        "meme_hits": meme_hits,
    }
    return round(score, 2), components


def detect_events(config_path: str | None = None, window_minutes: int | None = None, persist: bool = True) -> list[dict]:
    config = load_config(config_path)
    defaults = config.get("defaults", {})
    topics = config.get("topics", [])
    if not topics:
        return []

    max_window = max(int(topic.get("window_minutes") or window_minutes or defaults.get("window_minutes", 120)) for topic in topics)
    max_items = int(defaults.get("max_items", 500))
    now = utc_now()
    conn = get_db()
    recent = fetch_recent_tweets(conn, now - timedelta(minutes=max_window), max_items=max_items)

    events = []
    try:
        for topic in topics:
            topic_id = str(topic["id"])
            topic_name = str(topic.get("name") or topic_id)
            topic_window = int(topic.get("window_minutes") or window_minutes or defaults.get("window_minutes", 120))
            min_items = int(topic.get("min_items") or defaults.get("min_items", 2))
            min_score = float(topic.get("min_score") or defaults.get("min_score", 8))
            since = now - timedelta(minutes=topic_window)

            matches = []
            term_counts = Counter()
            domain_counts = Counter()
            author_counts = Counter()
            for item in recent:
                scraped_at = parse_iso(item.get("scraped_at"))
                if scraped_at and scraped_at < since:
                    continue
                item_score, item_meta = score_item_for_topic(item, topic)
                if item_score <= 0:
                    continue
                for term in item_meta.get("matched_keywords", []):
                    term_counts[term] += 1
                for domain in item_meta.get("matched_domains", []):
                    domain_counts[domain] += 1
                author = item.get("author_username") or ""
                if author:
                    author_counts[author] += 1
                match = dict(item)
                match["event_item_score"] = item_score
                match["event_item_metadata"] = item_meta
                matches.append(match)

            if len(matches) < min_items:
                continue

            matches.sort(
                key=lambda item: (
                    item.get("event_item_score", 0),
                    item.get("likes") or 0,
                    item.get("retweets") or 0,
                ),
                reverse=True,
            )
            score, score_components = score_event(matches, topic, defaults, author_counts, domain_counts)

            if score < min_score:
                continue

            event_id = _build_event_id(topic_id, matches)
            metadata = {
                "item_count": len(matches),
                "unique_authors": score_components["unique_authors"],
                "unique_link_domains": score_components["unique_link_domains"],
                "official_source_seen": score_components["official_source_seen"],
                "security_advisory_seen": score_components["security_advisory_seen"],
                "same_author_pileon": score_components["same_author_pileon"],
                "meme_only_cluster": score_components["meme_only_cluster"],
                "score_components": score_components,
                "top_terms": [term for term, _ in term_counts.most_common(10)],
                "top_domains": [domain for domain, _ in domain_counts.most_common(10)],
                "top_authors": [author for author, _ in author_counts.most_common(10)],
                "window_minutes": topic_window,
            }
            title = f"{topic_name}: {len(matches)} items from {score_components['unique_authors']} authors"
            summary = _build_summary(topic_name, matches, metadata)
            event = {
                "id": event_id,
                "topic_id": topic_id,
                "topic_name": topic_name,
                "window_start": iso(since),
                "window_end": iso(now),
                "score": score,
                "status": "new",
                "title": title,
                "summary": summary,
                "metadata": metadata,
                "items": matches[:12],
            }
            events.append(event)

            if persist:
                existing = conn.execute(
                    "SELECT status, delivered_at FROM detected_events WHERE id=?",
                    (event_id,),
                ).fetchone()
                status = existing["status"] if existing else "new"
                delivered_at = existing["delivered_at"] if existing else None
                conn.execute(
                    """
                    INSERT OR REPLACE INTO detected_events (
                        id, topic_id, topic_name, window_start, window_end, score,
                        status, title, summary, metadata, created_at, delivered_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id, topic_id, topic_name, event["window_start"],
                        event["window_end"], score, status, title, summary,
                        json.dumps(metadata), iso(now), delivered_at,
                    ),
                )
                conn.execute("DELETE FROM event_items WHERE event_id=?", (event_id,))
                for rank, item in enumerate(matches[:12], 1):
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO event_items (
                            event_id, item_type, item_id, rank, score, metadata
                        )
                        VALUES (?, 'tweet', ?, ?, ?, ?)
                        """,
                        (
                            event_id, item["id"], rank, item["event_item_score"],
                            json.dumps(item["event_item_metadata"]),
                        ),
                    )
        if persist:
            conn.commit()
    finally:
        conn.close()

    events.sort(key=lambda event: event["score"], reverse=True)
    return events


def list_events(status: str = "new", limit: int = 10) -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT * FROM detected_events
            WHERE (? = 'all' OR status = ?)
            ORDER BY score DESC, window_end DESC
            LIMIT ?
            """,
            (status, status, limit),
        ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["metadata"] = json.loads(event.get("metadata") or "{}")
            item_rows = conn.execute(
                """
                SELECT ei.*, t.author_username, t.author_display_name, t.text, t.likes,
                    t.retweets, t.replies, t.url, t.source, t.created_at, t.scraped_at
                FROM event_items ei
                JOIN tweets t ON t.id = ei.item_id
                WHERE ei.event_id=?
                ORDER BY ei.rank ASC
                """,
                (event["id"],),
            ).fetchall()
            event["items"] = []
            for item_row in item_rows:
                item = dict(item_row)
                item["metadata"] = json.loads(item.get("metadata") or "{}")
                event["items"].append(item)
            events.append(event)
        return events
    finally:
        conn.close()


def mark_delivered(event_ids: list[str]) -> int:
    if not event_ids:
        return 0
    conn = get_db()
    try:
        now = iso(utc_now())
        conn.executemany(
            "UPDATE detected_events SET status='delivered', delivered_at=? WHERE id=?",
            [(now, event_id) for event_id in event_ids],
        )
        conn.commit()
        return conn.total_changes
    finally:
        conn.close()


def format_markdown(events: list[dict], silent_empty: bool = True) -> str:
    if not events:
        return "[SILENT]" if silent_empty else "No candidate events."

    lines = ["# Touch Glass Candidate Events", ""]
    for event in events:
        metadata = event.get("metadata") or {}
        lines.append(f"## {event['title']}")
        lines.append(f"Score: {event['score']} | Window: {metadata.get('window_minutes', '?')}m | Topic: `{event['topic_id']}`")
        if event.get("summary"):
            lines.append(event["summary"])
        if metadata.get("top_domains"):
            lines.append("Domains: " + ", ".join(metadata["top_domains"][:6]))
        lines.append("")
        for item in event.get("items", [])[:5]:
            author = item.get("author_username") or item.get("author_display_name") or "?"
            text = _clean_text(item.get("text"), limit=240)
            metrics = []
            if item.get("likes"):
                metrics.append(f"{item['likes']} likes")
            if item.get("retweets"):
                metrics.append(f"{item['retweets']} reposts")
            metric_text = f" ({', '.join(metrics)})" if metrics else ""
            url = f" {item['url']}" if item.get("url") else ""
            lines.append(f"- @{author}: {text}{metric_text}{url}")
        lines.append("")
    return "\n".join(lines).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect and list Touch Glass candidate events.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    detect = sub.add_parser("detect", help="Detect events and persist them.")
    detect.add_argument("--config", help="Path to topics JSON config.")
    detect.add_argument("--window-minutes", type=int, help="Override topic windows.")
    detect.add_argument("--format", choices=["markdown", "json"], default="markdown")
    detect.add_argument("--limit", type=int, default=10)
    detect.add_argument("--mark-delivered", action="store_true")

    list_cmd = sub.add_parser("list", help="List persisted events.")
    list_cmd.add_argument("--status", default="new", choices=["new", "delivered", "all"])
    list_cmd.add_argument("--format", choices=["markdown", "json"], default="markdown")
    list_cmd.add_argument("--limit", type=int, default=10)
    list_cmd.add_argument("--mark-delivered", action="store_true")

    mark_cmd = sub.add_parser("mark-delivered", help="Mark event IDs as delivered.")
    mark_cmd.add_argument("event_ids", nargs="+")

    args = parser.parse_args(argv)
    init_db(verbose=False)

    if args.cmd == "detect":
        detect_events(config_path=args.config, window_minutes=args.window_minutes, persist=True)
        events = list_events(status="new", limit=args.limit)
        if args.format == "json":
            print(json.dumps({"count": len(events), "events": events}, indent=2))
        else:
            print(format_markdown(events))
        if args.mark_delivered:
            mark_delivered([event["id"] for event in events])
        return 0

    if args.cmd == "list":
        events = list_events(status=args.status, limit=args.limit)
        if args.format == "json":
            print(json.dumps({"count": len(events), "events": events}, indent=2))
        else:
            print(format_markdown(events, silent_empty=args.status == "new"))
        if args.mark_delivered:
            mark_delivered([event["id"] for event in events])
        return 0

    if args.cmd == "mark-delivered":
        count = mark_delivered(args.event_ids)
        print(f"Marked {count} event rows delivered")
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
