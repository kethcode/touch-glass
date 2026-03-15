# Touch Glass

Always-on social feed monitor for X/Twitter, LinkedIn, and Telegram. Scrapes your feeds via Chrome CDP, stores everything in a local database, enriches links, and gives you a searchable timeline with a self-hosted web UI.

## What it does

- **X/Twitter scraping** — Continuously scrapes your home timeline, bookmarks, and own tweets
- **Follower/following tracking** — Pages through your full follower and following lists, detects mutuals
- **LinkedIn monitoring** — Scrapes your LinkedIn feed and saved posts
- **Telegram monitoring** — Scrapes all your Telegram group chats, extracts messages and links
- **Telegram contacts** — Imports DM contacts and group participants, cross-matches with X/LinkedIn
- **Link enrichment** — Every URL found anywhere gets fetched, metadata extracted (title, description, OG tags)
- **Full-text search** — FTS5 + LIKE fallback across all posts, messages, links, and contacts
- **Semantic search** — Optional OpenAI embeddings for meaning-based search
- **Email digest** — Daily top 5 tweets + top 5 links, scored for AI/crypto/launches relevance
- **Web UI** — Password-protected timeline feed + search, works on mobile

## Architecture

```
touch-glass/
├── monitor.py          # Always-on orchestrator (10-min cycles)
├── scrapers/
│   ├── cdp.py          # Playwright CDP browser automation
│   ├── timeline.py     # X timeline, bookmarks, own tweets
│   ├── followers.py    # Follower/following lists + mutual detection
│   ├── linkedin.py     # LinkedIn feed + saved posts
│   └── telegram.py     # Telegram group chats + contacts
├── enrichment/
│   ├── links.py        # URL fetching + metadata extraction
│   ├── embeddings.py   # OpenAI vector embeddings
│   └── digest.py       # Daily email digest via Resend
├── api/
│   └── server.py       # FastAPI search + timeline API + self-hosted UI
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

Log into X, LinkedIn, and [Telegram Web](https://web.telegram.org/a/) in this Chrome window.

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
| Telegram groups | Every 3rd cycle | Scrape all Telegram group chats |
| Link enrichment | Every cycle | Fetch metadata for pending URLs |
| Embeddings | Every 4th cycle | Generate search embeddings |
| Email digest | Daily (7am) | Send top 5 tweets + top 5 links |

## Database

SQLite with:
- `tweets` — All posts from any platform (X, LinkedIn), with engagement stats and JSON metadata
- `contacts` — Followers, following, mutuals, Telegram contacts, LinkedIn connections
- `links` — First-class URL entities with title, description, content excerpts
- `messages` — Telegram group chat messages with author, chat name, links
- `conversations` — Conversation snippets from group chats (top messages per chat)
- `tweet_links` — Many-to-many tweet <> link associations
- `embeddings` — Vector embeddings for semantic search
- FTS5 indexes on tweets, links, contacts, and messages

## Telegram

Touch Glass connects to [Telegram Web](https://web.telegram.org/a/) via the same Chrome CDP connection. It:

1. **Scrapes all group chats** — clicks into each group, extracts messages (text only, no file downloads)
2. **Extracts contacts** — DM contacts and group participants, with cross-platform matching to X/LinkedIn
3. **Extracts links** — Every URL in any Telegram message becomes a first-class link entity
4. **Saves conversation snippets** — High-signal messages from each group, searchable via the API and UI

To set up: just log into https://web.telegram.org/a/ in the Chrome debug browser. The monitor will automatically detect the Telegram tab and start scraping.

## API

| Endpoint | Description |
|----------|------------|
| `GET /login` | Password login page |
| `GET /app` | Self-hosted search + timeline UI |
| `GET /timeline` | Reverse-chron feed of all items |
| `GET /search?q=` | Full-text search with LIKE fallback (tweets, links, contacts, messages) |
| `GET /stats` | Database statistics |
| `GET /tweets` | List tweets with filters |
| `GET /links` | List links with filters |
| `GET /contacts` | List contacts with filters |
| `GET /semantic?q=` | Semantic search (requires OpenAI key) |

All endpoints (except `/login` and `/app`) require `x-brain-key` header, `key` query param, or `brain_key` cookie.

## Email digest

Daily email with top 5 tweets + top 5 links, scored by relevance. Configurable focus areas (default: AI, stablecoins, crypto, GitHub repos, product launches). Requires a [Resend](https://resend.com) API key.

## License

MIT
