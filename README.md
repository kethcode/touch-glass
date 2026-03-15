# Touch Glass

Always-on social feed monitor for X/Twitter and LinkedIn. Scrapes your feeds via Chrome CDP, stores everything in a local database, enriches links, and gives you a searchable timeline.

## What it does

- **Timeline scraping** — Continuously scrapes your X home timeline, bookmarks, and own tweets
- **Follower/following tracking** — Pages through your full follower and following lists, detects mutuals
- **LinkedIn monitoring** — Scrapes your LinkedIn feed and saved posts
- **Link enrichment** — Every URL found in any post gets fetched, metadata extracted (title, description, OG tags)
- **Full-text search** — FTS5 + LIKE fallback across all posts, links, and contacts
- **Semantic search** — Optional OpenAI embeddings for meaning-based search
- **Email digest** — Daily AI-relevance-scored digest via Resend
- **Web UI** — Password-protected timeline feed + search, works on mobile

## Architecture

```
touch-glass/
├── monitor.py          # Always-on orchestrator (10-min cycles)
├── scrapers/
│   ├── cdp.py          # Playwright CDP browser automation
│   ├── timeline.py     # X timeline, bookmarks, own tweets
│   ├── followers.py    # Follower/following lists + mutual detection
│   └── linkedin.py     # LinkedIn feed + saved posts
├── enrichment/
│   ├── links.py        # URL fetching + metadata extraction
│   ├── embeddings.py   # OpenAI vector embeddings
│   └── digest.py       # Daily email digest via Resend
├── api/
│   └── server.py       # FastAPI search + timeline API
├── db/
│   └── schema.py       # SQLite schema + upsert helpers
└── frontend/
    └── public/
        └── index.html  # Web UI (timeline + search)
```

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your settings
```

### 3. Launch Chrome with remote debugging

```bash
# Quit Chrome first, then relaunch:
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/ChromeDebug" \
  '--remote-allow-origins=*'
```

Log into X and LinkedIn in this Chrome window.

### 4. Start

```bash
# Load env and start
set -a && source .env && set +a

# API server (background)
python -m uvicorn api.server:app --host 0.0.0.0 --port 8888 &

# Monitor (foreground)
python monitor.py
```

Open http://localhost:8888/login in your browser.

### Public access (optional)

Expose the API via Cloudflare Tunnel for mobile access:

```bash
brew install cloudflared
cloudflared tunnel --url http://localhost:8888
```

## Monitor cycle

Every 10 minutes:

| Task | Frequency | What it does |
|------|-----------|-------------|
| Timeline | Every cycle | Scrape X home feed (5 pages) |
| Bookmarks | Every cycle | Scrape X bookmarks (8 pages) |
| Own tweets | Every 3rd cycle | Scrape your profile |
| Followers | Every 6th cycle | Scroll through follower list |
| Following | Every 6th cycle | Scroll through following list |
| LinkedIn feed | Every 2nd cycle | Scrape LinkedIn feed |
| LinkedIn saved | Every 3rd cycle | Scrape LinkedIn saved posts |
| Link enrichment | Every cycle | Fetch metadata for pending URLs |
| Embeddings | Every 4th cycle | Generate search embeddings |
| Email digest | Daily | Send top-20 AI-relevant items |

## Database

SQLite with:
- `tweets` — All posts from any platform, with engagement stats and JSONB metadata
- `contacts` — Followers, following, mutuals with bios
- `links` — First-class URL entities with title, description, content excerpts
- `tweet_links` — Many-to-many tweet ↔ link associations
- `embeddings` — Vector embeddings for semantic search
- FTS5 indexes on tweets, links, and contacts

## API

| Endpoint | Description |
|----------|------------|
| `GET /timeline` | Reverse-chron feed of all items |
| `GET /search?q=` | Full-text search with LIKE fallback |
| `GET /stats` | Database statistics |
| `GET /tweets` | List tweets with filters |
| `GET /links` | List links with filters |
| `GET /contacts` | List contacts with filters |
| `GET /semantic?q=` | Semantic search (requires OpenAI key) |

All endpoints require `x-brain-key` header or `key` query param.

## License

MIT
