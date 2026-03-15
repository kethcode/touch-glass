"""
Link enrichment pipeline.

Fetches pending links, resolves redirects, extracts metadata (title, description,
OG tags, content excerpt). Links are first-class citizens — never without metadata.
"""

import json
import re
import ssl
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlparse

from db.schema import get_db


class MetaExtractor(HTMLParser):
    """Extract title, meta description, and OG tags from HTML."""

    def __init__(self):
        super().__init__()
        self.title = ""
        self.in_title = False
        self.meta = {}
        self.og = {}

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            name = attrs_dict.get("name", "").lower()
            prop = attrs_dict.get("property", "").lower()
            content = attrs_dict.get("content", "")
            if name == "description":
                self.meta["description"] = content
            elif prop.startswith("og:"):
                self.og[prop[3:]] = content
            elif name == "author":
                self.meta["author"] = content
            elif name == "keywords":
                self.meta["keywords"] = content

    def handle_data(self, data):
        if self.in_title:
            self.title += data

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False


def fetch_and_extract(url: str, timeout: int = 10) -> dict:
    """Fetch a URL and extract metadata."""
    result = {
        "resolved_url": url,
        "title": None,
        "description": None,
        "og_image": None,
        "content_excerpt": None,
        "content_type": None,
        "domain": urlparse(url).netloc,
    }

    try:
        import subprocess as _sp
        r = _sp.run([
            "curl", "-sL", "--max-time", str(timeout), "--max-filesize", "500000",
            "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "-H", "Accept: text/html,application/xhtml+xml,*/*",
            "-w", "\n__STATUS__%{http_code}\n__CONTENT_TYPE__%{content_type}\n__URL_EFFECTIVE__%{url_effective}",
            "-o", "-", url,
        ], capture_output=True, text=True, timeout=timeout + 5)

        output = r.stdout
        # Parse curl metadata from the end
        lines = output.rsplit("\n__STATUS__", 1)
        html = lines[0] if len(lines) > 1 else output
        meta_block = lines[1] if len(lines) > 1 else ""

        status = ""
        content_type = ""
        effective_url = url
        for line in meta_block.split("\n"):
            if line.startswith("__STATUS__"):
                continue
            elif "__CONTENT_TYPE__" in line:
                content_type = line.split("__CONTENT_TYPE__", 1)[1].strip()
            elif "__URL_EFFECTIVE__" in line:
                effective_url = line.split("__URL_EFFECTIVE__", 1)[1].strip()
            elif line.strip().isdigit():
                status = line.strip()

        result["resolved_url"] = effective_url
        result["domain"] = urlparse(effective_url).netloc
        result["content_type"] = content_type.split(";")[0].strip()

        if "text/html" not in content_type:
            return result

        # Parse HTML
        parser = MetaExtractor()
        try:
            parser.feed(html)
        except Exception:
            pass

        result["title"] = parser.title.strip() or parser.og.get("title")
        result["description"] = parser.og.get("description") or parser.meta.get("description")
        result["og_image"] = parser.og.get("image")

        # Extract text content for excerpt
        # Strip script/style tags first
        text = re.sub(r'<(script|style|nav|header|footer)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        # Take first 1000 chars as excerpt
        if text and len(text) > 50:
            result["content_excerpt"] = text[:1000]

    except Exception as e:
        result["error"] = str(e)[:200]

    return result


def enrich_pending_links(batch_size: int = 20) -> dict:
    """Fetch and enrich all pending links."""
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()

    pending = conn.execute(
        "SELECT id, url FROM links WHERE fetch_status = 'pending' LIMIT ?",
        (batch_size,)
    ).fetchall()

    if not pending:
        print("[links] No pending links to enrich")
        return {"task": "link_enrichment", "enriched": 0, "failed": 0}

    enriched = 0
    failed = 0

    for row in pending:
        link_id, url = row["id"], row["url"]

        # Skip known non-fetchable domains
        domain = urlparse(url).netloc
        if any(skip in domain for skip in ["t.co", "pic.twitter.com"]):
            conn.execute(
                "UPDATE links SET fetch_status='skipped', fetched_at=? WHERE id=?",
                (now, link_id)
            )
            continue

        meta = fetch_and_extract(url)

        if "error" in meta:
            conn.execute("""
                UPDATE links SET fetch_status='failed', fetched_at=?,
                    metadata=json_set(COALESCE(metadata,'{}'), '$.fetch_error', ?)
                WHERE id=?
            """, (now, meta["error"], link_id))
            failed += 1
        else:
            conn.execute("""
                UPDATE links SET
                    resolved_url=?, title=?, description=?, og_image=?,
                    content_excerpt=?, content_type=?, domain=?,
                    fetch_status='done', fetched_at=?
                WHERE id=?
            """, (
                meta["resolved_url"], meta["title"], meta["description"],
                meta["og_image"], meta["content_excerpt"], meta["content_type"],
                meta["domain"], now, link_id
            ))

            # Update FTS (content= tables need special delete command)
            rowid = conn.execute("SELECT rowid FROM links WHERE id=?", (link_id,)).fetchone()
            if rowid:
                try:
                    conn.execute("""
                        INSERT INTO links_fts(links_fts, rowid, title, description, content_excerpt, url, domain)
                        VALUES('delete', ?, '', '', '', '', '')
                    """, (rowid[0],))
                except Exception:
                    pass
                conn.execute("""
                    INSERT INTO links_fts(rowid, title, description, content_excerpt, url, domain)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (rowid[0], meta.get("title", ""), meta.get("description", ""),
                      meta.get("content_excerpt", ""), url, meta.get("domain", "")))

            enriched += 1

    conn.commit()
    conn.close()

    stats = {"task": "link_enrichment", "enriched": enriched, "failed": failed, "pending": len(pending)}
    print(f"[links] {stats}")
    return stats


if __name__ == "__main__":
    from db.schema import init_db
    init_db()
    enrich_pending_links(batch_size=50)
