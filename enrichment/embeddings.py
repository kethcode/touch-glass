"""
Generate embeddings for semantic search across all brain entities.

Uses OpenAI text-embedding-3-small (256 dims) for cost efficiency.
Stores embeddings in the DB for vector similarity search.
"""

import json
import os
import struct
from datetime import datetime, timezone

from db.schema import get_db

MODEL = "text-embedding-3-small"
DIMS = 256


def _get_openai_client():
    """Lazy import to avoid dependency issues."""
    try:
        from openai import OpenAI
        return OpenAI()
    except ImportError:
        raise RuntimeError("pip install openai to use embeddings")


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts."""
    client = _get_openai_client()
    resp = client.embeddings.create(
        model=MODEL,
        input=texts,
        dimensions=DIMS,
    )
    return [item.embedding for item in resp.data]


def _pack_embedding(vec: list[float]) -> bytes:
    """Pack float list into bytes."""
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack_embedding(data: bytes) -> list[float]:
    """Unpack bytes into float list."""
    n = len(data) // 4
    return list(struct.unpack(f"{n}f", data))


def embed_tweets(batch_size: int = 100) -> int:
    """Embed tweets that don't have embeddings yet."""
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()

    rows = conn.execute("""
        SELECT t.id, t.text, t.author_username, t.author_display_name
        FROM tweets t
        LEFT JOIN embeddings e ON e.entity_type='tweet' AND e.entity_id=t.id
        WHERE e.entity_id IS NULL AND t.text IS NOT NULL AND LENGTH(t.text) > 20
        LIMIT ?
    """, (batch_size,)).fetchall()

    if not rows:
        return 0

    texts = [f"{r['author_display_name'] or ''} (@{r['author_username'] or ''}): {r['text']}" for r in rows]
    ids = [r["id"] for r in rows]

    # Batch in groups of 50 for API limits
    embedded = 0
    for i in range(0, len(texts), 50):
        chunk_texts = texts[i:i+50]
        chunk_ids = ids[i:i+50]
        vectors = _embed_batch(chunk_texts)
        for eid, vec in zip(chunk_ids, vectors):
            conn.execute("""
                INSERT OR REPLACE INTO embeddings (entity_type, entity_id, embedding, model, created_at)
                VALUES ('tweet', ?, ?, ?, ?)
            """, (eid, _pack_embedding(vec), MODEL, now))
            embedded += 1
        conn.commit()

    conn.close()
    print(f"[embeddings] Embedded {embedded} tweets")
    return embedded


def embed_links(batch_size: int = 100) -> int:
    """Embed enriched links."""
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()

    rows = conn.execute("""
        SELECT l.id, l.url, l.title, l.description, l.content_excerpt, l.domain
        FROM links l
        LEFT JOIN embeddings e ON e.entity_type='link' AND e.entity_id=CAST(l.id AS TEXT)
        WHERE e.entity_id IS NULL AND l.fetch_status='done'
            AND (l.title IS NOT NULL OR l.description IS NOT NULL)
        LIMIT ?
    """, (batch_size,)).fetchall()

    if not rows:
        return 0

    texts = []
    ids = []
    for r in rows:
        parts = [r["domain"] or "", r["title"] or "", r["description"] or ""]
        if r["content_excerpt"]:
            parts.append(r["content_excerpt"][:500])
        texts.append(" | ".join(p for p in parts if p))
        ids.append(str(r["id"]))

    embedded = 0
    for i in range(0, len(texts), 50):
        chunk_texts = texts[i:i+50]
        chunk_ids = ids[i:i+50]
        vectors = _embed_batch(chunk_texts)
        for eid, vec in zip(chunk_ids, vectors):
            conn.execute("""
                INSERT OR REPLACE INTO embeddings (entity_type, entity_id, embedding, model, created_at)
                VALUES ('link', ?, ?, ?, ?)
            """, (eid, _pack_embedding(vec), MODEL, now))
            embedded += 1
        conn.commit()

    conn.close()
    print(f"[embeddings] Embedded {embedded} links")
    return embedded


def embed_contacts(batch_size: int = 100) -> int:
    """Embed contacts."""
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()

    rows = conn.execute("""
        SELECT c.id, c.username, c.display_name, c.bio, c.location
        FROM contacts c
        LEFT JOIN embeddings e ON e.entity_type='contact' AND e.entity_id=c.id
        WHERE e.entity_id IS NULL AND c.bio IS NOT NULL AND LENGTH(c.bio) > 10
        LIMIT ?
    """, (batch_size,)).fetchall()

    if not rows:
        return 0

    texts = [f"{r['display_name'] or ''} (@{r['username']}): {r['bio'] or ''} [{r['location'] or ''}]" for r in rows]
    ids = [r["id"] for r in rows]

    embedded = 0
    for i in range(0, len(texts), 50):
        chunk_texts = texts[i:i+50]
        chunk_ids = ids[i:i+50]
        vectors = _embed_batch(chunk_texts)
        for eid, vec in zip(chunk_ids, vectors):
            conn.execute("""
                INSERT OR REPLACE INTO embeddings (entity_type, entity_id, embedding, model, created_at)
                VALUES ('contact', ?, ?, ?, ?)
            """, (eid, _pack_embedding(vec), MODEL, now))
            embedded += 1
        conn.commit()

    conn.close()
    print(f"[embeddings] Embedded {embedded} contacts")
    return embedded


def semantic_search(query: str, entity_types: list[str] | None = None, top_k: int = 20) -> list[dict]:
    """Search across all embeddings using cosine similarity."""
    vectors = _embed_batch([query])
    query_vec = vectors[0]

    conn = get_db()
    type_filter = ""
    if entity_types:
        placeholders = ",".join("?" for _ in entity_types)
        type_filter = f"WHERE entity_type IN ({placeholders})"

    rows = conn.execute(f"""
        SELECT entity_type, entity_id, embedding FROM embeddings {type_filter}
    """, entity_types or []).fetchall()

    # Compute cosine similarity
    results = []
    for row in rows:
        vec = _unpack_embedding(row["embedding"])
        dot = sum(a * b for a, b in zip(query_vec, vec))
        norm_q = sum(a * a for a in query_vec) ** 0.5
        norm_v = sum(a * a for a in vec) ** 0.5
        if norm_q > 0 and norm_v > 0:
            sim = dot / (norm_q * norm_v)
        else:
            sim = 0
        results.append({
            "entity_type": row["entity_type"],
            "entity_id": row["entity_id"],
            "score": sim,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:top_k]

    # Hydrate with actual data
    for r in results:
        if r["entity_type"] == "tweet":
            tweet = conn.execute("SELECT * FROM tweets WHERE id=?", (r["entity_id"],)).fetchone()
            if tweet:
                r["data"] = dict(tweet)
        elif r["entity_type"] == "link":
            link = conn.execute("SELECT * FROM links WHERE id=?", (int(r["entity_id"]),)).fetchone()
            if link:
                r["data"] = dict(link)
        elif r["entity_type"] == "contact":
            contact = conn.execute("SELECT * FROM contacts WHERE id=?", (r["entity_id"],)).fetchone()
            if contact:
                r["data"] = dict(contact)

    conn.close()
    return results


if __name__ == "__main__":
    import sys
    from db.schema import init_db
    init_db()

    if len(sys.argv) > 1 and sys.argv[1] == "search":
        query = " ".join(sys.argv[2:])
        results = semantic_search(query)
        for r in results:
            print(f"[{r['score']:.3f}] {r['entity_type']}:{r['entity_id']}")
            if "data" in r:
                if r["entity_type"] == "tweet":
                    print(f"  @{r['data'].get('author_username')}: {r['data'].get('text', '')[:100]}")
                elif r["entity_type"] == "link":
                    print(f"  {r['data'].get('title', '')} — {r['data'].get('url', '')}")
                elif r["entity_type"] == "contact":
                    print(f"  @{r['data'].get('username')}: {r['data'].get('bio', '')[:100]}")
            print()
    else:
        embed_tweets()
        embed_links()
        embed_contacts()
