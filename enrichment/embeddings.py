"""
Generate embeddings for semantic search across all brain entities.

Supports OpenAI embeddings and local OpenAI-compatible embedding servers
such as llama.cpp's /v1/embeddings endpoint.
"""

import json
import os
import struct
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from db.schema import get_db

DEFAULT_OPENAI_MODEL = "text-embedding-3-small"
DEFAULT_LOCAL_MODEL = "Qwen3-Embedding-0.6B-Q8_0"
DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:8080/v1"


def _normalize_provider(raw: str | None = None) -> str:
    value = (raw if raw is not None else os.environ.get("EMBEDDING_PROVIDER", "auto")).strip().lower()
    aliases = {
        "": "auto",
        "none": "disabled",
        "off": "disabled",
        "false": "disabled",
        "0": "disabled",
        "local": "llamacpp",
        "llama": "llamacpp",
        "llama.cpp": "llamacpp",
        "llama_cpp": "llamacpp",
    }
    value = aliases.get(value, value)
    if value == "auto":
        return "openai" if os.environ.get("OPENAI_API_KEY") else "disabled"
    return value


def _embedding_provider() -> str:
    provider = _normalize_provider()
    if provider not in {"disabled", "openai", "llamacpp"}:
        raise RuntimeError(f"Unsupported EMBEDDING_PROVIDER={provider!r}")
    return provider


def _embedding_model(provider: str | None = None) -> str:
    provider = provider or _embedding_provider()
    model = os.environ.get("EMBEDDING_MODEL", "").strip()
    if model:
        return model
    if provider == "openai":
        return os.environ.get("OPENAI_EMBEDDING_MODEL", DEFAULT_OPENAI_MODEL)
    if provider == "llamacpp":
        return DEFAULT_LOCAL_MODEL
    return "disabled"


def _embedding_dims(provider: str | None = None) -> int | None:
    provider = provider or _embedding_provider()
    raw = os.environ.get("EMBEDDING_DIMS", "").strip().lower()
    if raw == "":
        return 256 if provider == "openai" else None
    if raw in {"native", "none", "null"}:
        return None
    try:
        dims = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid EMBEDDING_DIMS={raw!r}") from exc
    if dims <= 0:
        raise RuntimeError("EMBEDDING_DIMS must be positive")
    return dims


def _model_key() -> str:
    """Stable key for comparing only embeddings from the same vector space."""
    provider = _embedding_provider()
    model = _embedding_model(provider)
    dims = _embedding_dims(provider)
    return f"{provider}:{model}:dims={dims or 'native'}"


def embedding_status() -> dict:
    provider = _embedding_provider()
    status = {
        "provider": provider,
        "model": _embedding_model(provider),
        "dimensions": _embedding_dims(provider),
        "model_key": _model_key(),
        "enabled": False,
    }
    if provider == "disabled":
        return status
    if provider == "openai":
        status["enabled"] = bool(os.environ.get("OPENAI_API_KEY")) and _openai_package_available()
        status["missing"] = []
        if not os.environ.get("OPENAI_API_KEY"):
            status["missing"].append("OPENAI_API_KEY")
        if not _openai_package_available():
            status["missing"].append("openai package")
        return status
    if provider == "llamacpp":
        status["base_url"] = _embedding_base_url()
        status["enabled"] = True
        return status
    return status


def embeddings_enabled() -> bool:
    return bool(embedding_status()["enabled"])


def _openai_package_available() -> bool:
    try:
        import openai  # noqa: F401
        return True
    except ImportError:
        return False


def _get_openai_client():
    try:
        from openai import OpenAI
        return OpenAI()
    except ImportError as exc:
        raise RuntimeError("pip install openai to use OpenAI embeddings") from exc


def _embedding_base_url() -> str:
    return os.environ.get("EMBEDDING_BASE_URL", DEFAULT_LOCAL_BASE_URL).rstrip("/")


def _embedding_endpoint() -> str:
    base = _embedding_base_url()
    if base.endswith("/embeddings"):
        return base
    if base.endswith("/v1"):
        return f"{base}/embeddings"
    return f"{base}/v1/embeddings"


def _max_input_chars() -> int:
    raw = os.environ.get("EMBEDDING_MAX_CHARS", "6000").strip()
    try:
        return max(500, int(raw))
    except ValueError:
        return 6000


def _is_qwen_embedding_model(model: str) -> bool:
    normalized = model.lower()
    return "qwen" in normalized and "embed" in normalized


def _format_text(text: str, is_query: bool) -> str:
    text = (text or "").strip()
    max_chars = _max_input_chars()
    if len(text) > max_chars:
        text = text[:max_chars]

    provider = _embedding_provider()
    if provider != "llamacpp":
        return text

    model = _embedding_model(provider)
    if _is_qwen_embedding_model(model):
        if is_query:
            instruction = os.environ.get(
                "EMBEDDING_QUERY_INSTRUCTION",
                "Retrieve relevant social feed items, links, and contacts for the given query.",
            ).strip()
            return f"Instruct: {instruction}\nQuery: {text}"
        return text

    if is_query:
        return f"task: search result | query: {text}"
    return f"title: none | text: {text}"


def _embed_openai(texts: list[str]) -> list[list[float]]:
    client = _get_openai_client()
    provider = "openai"
    payload = {
        "model": _embedding_model(provider),
        "input": texts,
    }
    dims = _embedding_dims(provider)
    if dims:
        payload["dimensions"] = dims
    resp = client.embeddings.create(**payload)
    return [item.embedding for item in resp.data]


def _embed_llamacpp(texts: list[str]) -> list[list[float]]:
    payload = json.dumps({
        "model": _embedding_model("llamacpp"),
        "input": texts,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("EMBEDDING_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout = float(os.environ.get("EMBEDDING_TIMEOUT", "120"))
    req = urllib.request.Request(_embedding_endpoint(), data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Embedding server returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Embedding server unavailable at {_embedding_endpoint()}: {exc.reason}") from exc

    data = json.loads(body)
    items = data.get("data")
    if not isinstance(items, list):
        raise RuntimeError(f"Unexpected embedding response: {body[:500]}")

    items.sort(key=lambda item: item.get("index", 0))
    vectors = [item.get("embedding") for item in items]
    if len(vectors) != len(texts) or any(not isinstance(vec, list) for vec in vectors):
        raise RuntimeError(f"Unexpected embedding count from server: got {len(vectors)}, expected {len(texts)}")
    return vectors


def _embed_batch(texts: list[str], is_query: bool = False) -> list[list[float]]:
    """Embed a batch of texts with the configured provider."""
    if not texts:
        return []
    provider = _embedding_provider()
    if provider == "disabled":
        raise RuntimeError("Embeddings are disabled; set EMBEDDING_PROVIDER=openai or EMBEDDING_PROVIDER=llamacpp")

    formatted = [_format_text(text, is_query=is_query) for text in texts]
    if provider == "openai":
        return _embed_openai(formatted)
    if provider == "llamacpp":
        return _embed_llamacpp(formatted)
    raise RuntimeError(f"Unsupported embedding provider: {provider}")


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
    model_key = _model_key()

    rows = conn.execute("""
        SELECT t.id, t.text, t.author_username, t.author_display_name
        FROM tweets t
        LEFT JOIN embeddings e ON e.entity_type='tweet' AND e.entity_id=t.id AND e.model=?
        WHERE e.entity_id IS NULL AND t.text IS NOT NULL AND LENGTH(t.text) > 20
        LIMIT ?
    """, (model_key, batch_size)).fetchall()

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
            """, (eid, _pack_embedding(vec), model_key, now))
            embedded += 1
        conn.commit()

    conn.close()
    print(f"[embeddings] Embedded {embedded} tweets")
    return embedded


def embed_links(batch_size: int = 100) -> int:
    """Embed enriched links."""
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    model_key = _model_key()

    rows = conn.execute("""
        SELECT l.id, l.url, l.title, l.description, l.content_excerpt, l.domain
        FROM links l
        LEFT JOIN embeddings e ON e.entity_type='link' AND e.entity_id=CAST(l.id AS TEXT) AND e.model=?
        WHERE e.entity_id IS NULL AND l.fetch_status='done'
            AND (l.title IS NOT NULL OR l.description IS NOT NULL)
        LIMIT ?
    """, (model_key, batch_size)).fetchall()

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
            """, (eid, _pack_embedding(vec), model_key, now))
            embedded += 1
        conn.commit()

    conn.close()
    print(f"[embeddings] Embedded {embedded} links")
    return embedded


def embed_contacts(batch_size: int = 100) -> int:
    """Embed contacts."""
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    model_key = _model_key()

    rows = conn.execute("""
        SELECT c.id, c.username, c.display_name, c.bio, c.location
        FROM contacts c
        LEFT JOIN embeddings e ON e.entity_type='contact' AND e.entity_id=c.id AND e.model=?
        WHERE e.entity_id IS NULL AND c.bio IS NOT NULL AND LENGTH(c.bio) > 10
        LIMIT ?
    """, (model_key, batch_size)).fetchall()

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
            """, (eid, _pack_embedding(vec), model_key, now))
            embedded += 1
        conn.commit()

    conn.close()
    print(f"[embeddings] Embedded {embedded} contacts")
    return embedded


def semantic_search(query: str, entity_types: list[str] | None = None, top_k: int = 20) -> list[dict]:
    """Search across all embeddings using cosine similarity."""
    model_key = _model_key()
    vectors = _embed_batch([query], is_query=True)
    query_vec = vectors[0]

    conn = get_db()
    type_filter = "WHERE model=?"
    params = [model_key]
    if entity_types:
        placeholders = ",".join("?" for _ in entity_types)
        type_filter += f" AND entity_type IN ({placeholders})"
        params.extend(entity_types)

    rows = conn.execute(f"""
        SELECT entity_type, entity_id, embedding FROM embeddings {type_filter}
    """, params).fetchall()

    # Compute cosine similarity
    results = []
    for row in rows:
        vec = _unpack_embedding(row["embedding"])
        if len(vec) != len(query_vec):
            continue
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

    if len(sys.argv) > 1 and sys.argv[1] == "status":
        print(json.dumps(embedding_status(), indent=2))
        raise SystemExit(0)

    from db.schema import init_db
    init_db()

    if not embeddings_enabled():
        print(json.dumps(embedding_status(), indent=2))
        raise SystemExit("Embeddings are disabled or unavailable")

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
