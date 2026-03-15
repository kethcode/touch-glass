#!/usr/bin/env python3
"""
Touch Glass — Social feed monitor API.

- Timeline feed (reverse-chron across all sources)
- Full-text + LIKE fallback search
- Stats, filters, semantic search
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import re
from datetime import datetime
from fastapi import FastAPI, Query, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import json

from db.schema import get_db, init_db

API_PASSWORD = os.environ.get("BRAIN_PASSWORD", "changeme")

app = FastAPI(title="Touch Glass", description="Social feed monitor")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], expose_headers=["*"])


@app.middleware("http")
async def auth_middleware(request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)
    if request.url.path in ("/login", "/app"):
        return await call_next(request)
    auth = (request.headers.get("x-brain-key")
            or request.query_params.get("key")
            or request.cookies.get("brain_key"))
    if auth != API_PASSWORD:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


@app.on_event("startup")
def startup():
    init_db()


@app.get("/login")
def login_page():
    return HTMLResponse("""
    <html><head><title>Touch Glass</title><meta name="viewport" content="width=device-width,initial-scale=1">
    <style>
        body { background:#0d1117; color:#c9d1d9; font-family:-apple-system,sans-serif; display:flex; align-items:center; justify-content:center; min-height:100vh; margin:0; }
        .box { background:#161b22; border:1px solid #30363d; border-radius:12px; padding:32px; width:320px; text-align:center; }
        h1 { color:#58a6ff; font-size:24px; margin-bottom:20px; }
        input { width:100%; padding:12px; border-radius:8px; border:1px solid #30363d; background:#0d1117; color:#c9d1d9; font-size:16px; margin-bottom:16px; box-sizing:border-box; }
        button { width:100%; padding:12px; border-radius:8px; border:none; background:#58a6ff; color:#fff; font-size:16px; cursor:pointer; }
        .err { color:#f85149; font-size:14px; margin-top:8px; display:none; }
    </style></head><body>
    <div class="box">
        <h1>Touch Glass</h1>
        <input type="password" id="pw" placeholder="Password" autofocus onkeydown="if(event.key==='Enter')go()">
        <button onclick="go()">Enter</button>
        <div class="err" id="err">Wrong password</div>
    </div>
    <script>
    function go() {
        const pw = document.getElementById('pw').value;
        document.cookie = 'brain_key=' + pw + ';path=/;max-age=31536000';
        fetch('/stats', {headers:{'x-brain-key':pw}}).then(r => {
            if (r.ok) { window.location.href = '/app?key=' + encodeURIComponent(pw); }
            else { document.getElementById('err').style.display = 'block'; }
        }).catch(() => { document.getElementById('err').style.display = 'block'; });
    }
    </script></body></html>
    """)


@app.get("/app")
def search_app():
    frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "public", "index.html")
    if os.path.exists(frontend_path):
        with open(frontend_path) as f:
            html = f.read()
        # Strip any hardcoded API URLs so it uses relative paths
        html = re.sub(r"https?://[a-z0-9-]+\.trycloudflare\.com", "", html)
        html = re.sub(r"https?://[a-z0-9-]+\.loca\.lt", "", html)
        return HTMLResponse(html)
    return HTMLResponse("<h1>Frontend not found</h1>", status_code=404)


# ---- Timeline feed ----

@app.get("/timeline")
def timeline(
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    platform: str = Query(None),
    source: str = Query(None),
):
    """Reverse-chron feed of all items: tweets, contacts, links."""
    conn = get_db()
    items = []

    # Tweets / posts
    sql = """SELECT id, platform, author_username, author_display_name, text,
                likes, retweets, replies, views, url, source, created_at, scraped_at,
                'tweet' as type
             FROM tweets WHERE 1=1"""
    params = []
    if platform:
        sql += " AND platform=?"
        params.append(platform)
    if source:
        sql += " AND source=?"
        params.append(source)

    # Contacts (recently scraped)
    contact_sql = """SELECT id, platform, username, display_name, bio,
                followers_count, following_count, relationship, profile_image,
                scraped_at, scraped_at as created_at,
                'contact' as type
             FROM contacts WHERE 1=1"""
    c_params = []
    if platform:
        contact_sql += " AND platform=?"
        c_params.append(platform)

    # Links (recently found)
    link_sql = """SELECT CAST(id AS TEXT) as id, platform, domain, '' as author_username,
                title as display_name, description as text,
                url, fetch_status, created_at, created_at as scraped_at,
                'link' as type
             FROM links WHERE 1=1"""
    l_params = []

    # Union all and sort by scraped_at desc
    if source:
        # Only tweets when filtering by source
        union = f"SELECT * FROM ({sql}) ORDER BY scraped_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    else:
        union = f"""
            SELECT * FROM (
                SELECT id, platform, author_username, '' as bio, text, source, url, created_at, scraped_at, type FROM ({sql})
                UNION ALL
                SELECT id, platform, username as author_username, bio, '' as text, relationship as source, '' as url, created_at, scraped_at, type FROM ({contact_sql})
                UNION ALL
                SELECT id, platform, author_username, '' as bio, text, 'link' as source, url, created_at, scraped_at, type FROM ({link_sql})
            ) ORDER BY scraped_at DESC LIMIT ? OFFSET ?
        """
        params = params + c_params + l_params + [limit, offset]

    try:
        rows = conn.execute(union, params).fetchall()
        items = [dict(r) for r in rows]
    except Exception:
        # Fallback: just tweets
        sql += " ORDER BY scraped_at DESC LIMIT ? OFFSET ?"
        params_fb = []
        if platform:
            params_fb.append(platform)
        if source:
            params_fb.append(source)
        params_fb.extend([limit, offset])
        rows = conn.execute(
            """SELECT *, 'tweet' as type FROM tweets WHERE 1=1
               {} {} ORDER BY scraped_at DESC LIMIT ? OFFSET ?""".format(
                "AND platform=?" if platform else "",
                "AND source=?" if source else "",
            ), params_fb
        ).fetchall()
        items = [dict(r) for r in rows]

    conn.close()
    return {"count": len(items), "items": items}


# ---- Improved search ----

def _safe_fts(q: str) -> str:
    """Build a safe FTS5 query string."""
    # Remove special FTS characters
    clean = re.sub(r'[^\w\s]', ' ', q)
    words = [w.strip() for w in clean.split() if w.strip() and len(w.strip()) >= 2]
    if not words:
        return None
    # Use OR with prefix matching
    return " OR ".join(f'"{w}"*' for w in words)


@app.get("/search")
def search(
    q: str = Query(..., min_length=1),
    types: str = Query("all"),
    platform: str = Query(None),
    source: str = Query(None),
    limit: int = Query(50, le=200),
):
    """Search with FTS5 + LIKE fallback."""
    conn = get_db()
    results = []
    search_types = types.split(",") if types != "all" else ["tweet", "link", "contact"]
    fts_q = _safe_fts(q)

    if "tweet" in search_types:
        found = set()
        # Try FTS first
        if fts_q:
            try:
                sql = "SELECT t.* FROM tweets_fts f JOIN tweets t ON t.rowid = f.rowid WHERE tweets_fts MATCH ?"
                params = [fts_q]
                if platform:
                    sql += " AND t.platform=?"
                    params.append(platform)
                if source:
                    sql += " AND t.source=?"
                    params.append(source)
                sql += f" ORDER BY t.likes DESC LIMIT {limit}"
                for row in conn.execute(sql, params).fetchall():
                    r = dict(row)
                    r["type"] = "tweet"
                    results.append(r)
                    found.add(r["id"])
            except Exception:
                pass

        # LIKE fallback for remaining slots
        remaining = limit - len(found)
        if remaining > 0:
            like_q = f"%{q}%"
            sql = "SELECT * FROM tweets WHERE (text LIKE ? OR author_username LIKE ? OR author_display_name LIKE ?)"
            params = [like_q, like_q, like_q]
            if platform:
                sql += " AND platform=?"
                params.append(platform)
            if source:
                sql += " AND source=?"
                params.append(source)
            if found:
                placeholders = ",".join("?" for _ in found)
                sql += f" AND id NOT IN ({placeholders})"
                params.extend(found)
            sql += f" ORDER BY likes DESC LIMIT {remaining}"
            for row in conn.execute(sql, params).fetchall():
                r = dict(row)
                r["type"] = "tweet"
                results.append(r)

    if "link" in search_types:
        found_links = set()
        if fts_q:
            try:
                sql = "SELECT l.* FROM links_fts f JOIN links l ON l.rowid = f.rowid WHERE links_fts MATCH ?"
                sql += f" LIMIT {limit}"
                for row in conn.execute(sql, [fts_q]).fetchall():
                    r = dict(row)
                    r["type"] = "link"
                    results.append(r)
                    found_links.add(r["id"])
            except Exception:
                pass

        remaining = limit - len(found_links)
        if remaining > 0:
            like_q = f"%{q}%"
            sql = "SELECT * FROM links WHERE (url LIKE ? OR title LIKE ? OR description LIKE ? OR domain LIKE ?)"
            params = [like_q, like_q, like_q, like_q]
            if found_links:
                placeholders = ",".join("?" for _ in found_links)
                sql += f" AND id NOT IN ({placeholders})"
                params.extend(found_links)
            sql += f" LIMIT {remaining}"
            for row in conn.execute(sql, params).fetchall():
                r = dict(row)
                r["type"] = "link"
                results.append(r)

    if "contact" in search_types:
        found_contacts = set()
        if fts_q:
            try:
                sql = "SELECT c.* FROM contacts_fts f JOIN contacts c ON c.rowid = f.rowid WHERE contacts_fts MATCH ?"
                sql += f" LIMIT {limit}"
                for row in conn.execute(sql, [fts_q]).fetchall():
                    r = dict(row)
                    r["type"] = "contact"
                    results.append(r)
                    found_contacts.add(r["id"])
            except Exception:
                pass

        remaining = limit - len(found_contacts)
        if remaining > 0:
            like_q = f"%{q}%"
            sql = "SELECT * FROM contacts WHERE (username LIKE ? OR display_name LIKE ? OR bio LIKE ?)"
            params = [like_q, like_q, like_q]
            if found_contacts:
                placeholders = ",".join("?" for _ in found_contacts)
                sql += f" AND id NOT IN ({placeholders})"
                params.extend(found_contacts)
            sql += f" LIMIT {remaining}"
            for row in conn.execute(sql, params).fetchall():
                r = dict(row)
                r["type"] = "contact"
                results.append(r)

    conn.close()
    return {"query": q, "count": len(results), "results": results}


@app.get("/semantic")
def semantic_search_endpoint(
    q: str = Query(..., min_length=1),
    types: str = Query(None),
    limit: int = Query(20, le=100),
):
    try:
        from enrichment.embeddings import semantic_search
        entity_types = types.split(",") if types else None
        results = semantic_search(q, entity_types=entity_types, top_k=limit)
        return {"query": q, "count": len(results), "results": results}
    except Exception as e:
        return {"error": str(e)}


@app.get("/stats")
def stats():
    conn = get_db()
    result = {
        "tweets": conn.execute("SELECT COUNT(*) as c FROM tweets").fetchone()["c"],
        "links": conn.execute("SELECT COUNT(*) as c FROM links").fetchone()["c"],
        "contacts": conn.execute("SELECT COUNT(*) as c FROM contacts").fetchone()["c"],
        "pending_links": conn.execute("SELECT COUNT(*) as c FROM links WHERE fetch_status='pending'").fetchone()["c"],
        "enriched_links": conn.execute("SELECT COUNT(*) as c FROM links WHERE fetch_status='done'").fetchone()["c"],
        "embeddings": conn.execute("SELECT COUNT(*) as c FROM embeddings").fetchone()["c"],
        "by_platform": {r["platform"]: r["c"] for r in conn.execute("SELECT platform, COUNT(*) as c FROM tweets GROUP BY platform").fetchall()},
        "by_source": {r["source"]: r["c"] for r in conn.execute("SELECT source, COUNT(*) as c FROM tweets GROUP BY source").fetchall()},
    }
    conn.close()
    return result


@app.get("/tweets")
def list_tweets(source: str = Query(None), platform: str = Query(None), author: str = Query(None), limit: int = Query(50, le=500), offset: int = Query(0)):
    conn = get_db()
    sql = "SELECT * FROM tweets WHERE 1=1"
    params = []
    if source:
        sql += " AND source=?"
        params.append(source)
    if platform:
        sql += " AND platform=?"
        params.append(platform)
    if author:
        sql += " AND author_username=?"
        params.append(author)
    sql += " ORDER BY scraped_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return {"count": len(rows), "tweets": rows}


@app.get("/links")
def list_links(status: str = Query(None), domain: str = Query(None), platform: str = Query(None), limit: int = Query(50, le=500)):
    conn = get_db()
    sql = "SELECT * FROM links WHERE 1=1"
    params = []
    if status:
        sql += " AND fetch_status=?"
        params.append(status)
    if domain:
        sql += " AND domain LIKE ?"
        params.append(f"%{domain}%")
    if platform:
        sql += " AND platform=?"
        params.append(platform)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return {"count": len(rows), "links": rows}


@app.get("/contacts")
def list_contacts(relationship: str = Query(None), platform: str = Query(None), limit: int = Query(50, le=500)):
    conn = get_db()
    sql = "SELECT * FROM contacts WHERE 1=1"
    params = []
    if relationship:
        sql += " AND relationship=?"
        params.append(relationship)
    if platform:
        sql += " AND platform=?"
        params.append(platform)
    sql += " ORDER BY scraped_at DESC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return {"count": len(rows), "contacts": rows}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8888)
