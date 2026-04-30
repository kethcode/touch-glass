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
- **Semantic search** — Optional local llama.cpp or OpenAI embeddings for meaning-based search
- **Optional email digest** — Daily top tweets + links via Resend when configured
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
│   ├── embeddings.py   # Local/OpenAI vector embeddings
│   ├── events.py       # Topic/event detector for agent handoff
│   └── digest.py       # Optional daily email digest via Resend
├── api/
│   └── server.py       # FastAPI search + timeline API + self-hosted UI
├── db/
│   └── schema.py       # SQLite schema + upsert helpers
└── frontend/
    └── public/
        └── index.html  # Web UI (timeline + search)
```

## Setup

For a full Ubuntu 24.04 service install, see [DEPLOYMENT.md](DEPLOYMENT.md).

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

### 3. Optional: local embeddings

Semantic search can run without a paid API by pointing Touch Glass at a local
OpenAI-compatible embedding server. On an Ubuntu 24.04 CPU-only box:

```bash
# Build llama.cpp for the host CPU.
sudo apt update
sudo apt install -y build-essential cmake git curl libcurl4-openssl-dev
mkdir -p ~/src
git clone https://github.com/ggml-org/llama.cpp.git ~/src/llama.cpp
cd ~/src/llama.cpp
cmake -B build -DGGML_NATIVE=ON -DGGML_CURL=ON -DLLAMA_CURL=ON
cmake --build build --config Release -j

# Serve Qwen3-Embedding-0.6B locally.
./build/bin/llama-server \
  --hf-repo Qwen/Qwen3-Embedding-0.6B-GGUF \
  --hf-file Qwen3-Embedding-0.6B-Q8_0.gguf \
  --embedding \
  --pooling last \
  --host 127.0.0.1 \
  --port 8080 \
  -c 2048 \
  -ub 8192 \
  --threads 16
```

Then set:

```bash
EMBEDDING_PROVIDER=llamacpp
EMBEDDING_BASE_URL=http://127.0.0.1:8080/v1
EMBEDDING_MODEL=Qwen3-Embedding-0.6B-Q8_0
EMBEDDING_DIMS=native
```

Notes:
- No GPU is required. On a Ryzen CPU, first-time backfill is the slow part; ongoing batches are small.
- `--threads 16` is a reasonable starting point for a Ryzen 9 9955HX-class CPU. Benchmark lower or higher values after the server is running.
- Keep the model files on the data drive if the OS drive is space-constrained. The Python app only talks to the local HTTP server, so macOS ARM development and Ubuntu AMD64 deployment use the same app code.
- For a clean install there is no migration step. If you experiment with models early on, deleting `brain.db` is the simplest reset.

For OpenAI instead:

```bash
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=...
# Optional; defaults to text-embedding-3-small with 256 dimensions.
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMS=256
```

Check the active embedding configuration:

```bash
python enrichment/embeddings.py status
```

### 4. Optional: Hermes handoff

Touch Glass can reduce the raw feed into candidate events for a separate agent
to interpret and deliver. Tune the grouped topic files under `topics/`; the
active import list starts at `topics/index.json`.

```bash
nano topics/index.json
nano topics/priority/wildcat.json
```

Detect recent topic events and print a compact Markdown bundle:

```bash
python -m enrichment.events detect --config topics/index.json --format markdown
```

Candidate event scoring emphasizes source diversity rather than raw volume:

```text
score =
  1.0 * matching_posts
+ 2.0 * unique_authors
+ 2.0 * unique_link_domains
+ 4.0 * official_source_seen
+ 5.0 * security_advisory_seen
- 3.0 * same_author_pileon
- 2.0 * meme_only_cluster
```

If no event crosses threshold, the command prints `[SILENT]`. This is intended
for Hermes cron jobs, which suppress delivery when a successful run starts with
that marker.

For Hermes delivery, tune the versioned prompt files:

```bash
nano prompts/hermes_consolidation.md
nano prompts/hermes_commentary.md
```

The consolidation prompt handles duplicate detector topics and evidence merging.
The commentary prompt controls the final digest voice.

Then test the wrapper script:

```bash
scripts/hermes_event_briefing.sh
```

Routine Hermes runs use a 120-minute detector window by default. For a one-off
catch-up or tuning pass, set `TOUCH_GLASS_HERMES_WINDOW_MINUTES` on that run.
If routine runs are still too chatty, set `TOUCH_GLASS_HERMES_MIN_PRIORITY`
before reaching for a score gate; broad topics can score high by volume.

See [HERMES.md](HERMES.md) for the cron handoff shape. The stable command for
Hermes is:

```text
/path/to/touch-glass/scripts/hermes_event_briefing.sh
```

For debugging:

```bash
python -m enrichment.events detect --config topics/index.json --format json
python -m enrichment.events list --status all --format markdown
```

### 5. Launch Chrome with remote debugging

```bash
# Quit Chrome first, then relaunch:
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/ChromeDebug" \
  '--remote-allow-origins=*'
```

Log into X, LinkedIn, and [Telegram Web](https://web.telegram.org/a/) in this Chrome window.

### 6. Start

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
| Optional email digest | Daily (7am) | Generate top tweets/links; sends email only if Resend is configured |

## Operations

### Twitter-only server

On the current Ubuntu server deployment, Touch Glass can run as user-level
systemd services under the `lain` account:

```bash
systemctl --user status touch-glass-xvfb touch-glass-chrome touch-glass-api touch-glass-monitor --no-pager
systemctl --user restart touch-glass-monitor
journalctl --user -u touch-glass-monitor -f
```

Make sure lingering is enabled so the user services survive logout and reboot:

```bash
sudo loginctl enable-linger lain
loginctl show-user lain -p Linger
```

For `.env` changes affecting scraping, restart the monitor:

```bash
systemctl --user restart touch-glass-monitor
```

For API password or API server changes, restart the API:

```bash
systemctl --user restart touch-glass-api
```

For browser/session changes, restart Chrome CDP:

```bash
systemctl --user restart touch-glass-chrome
curl -s http://127.0.0.1:9222/json/version | python3 -m json.tool
```

### Topic edits

Edit the active topic configuration:

```bash
nano topics/index.json
```

Topic edits do not require restarting the monitor. The event detector reads the
config file each time it runs. Test the current config with:

```bash
set -a && source .env && set +a
.venv/bin/python -m enrichment.events detect --config topics/index.json --format markdown
```

If the output is `[SILENT]`, no topic cluster crossed the configured threshold.

## Database

SQLite with:
- `tweets` — All posts from any platform (X, LinkedIn), with engagement stats and JSON metadata
- `contacts` — Followers, following, mutuals, Telegram contacts, LinkedIn connections
- `links` — First-class URL entities with title, description, content excerpts
- `messages` — Telegram group chat messages with author, chat name, links
- `conversations` — Conversation snippets from group chats (top messages per chat)
- `tweet_links` — Many-to-many tweet <> link associations
- `embeddings` — Vector embeddings for semantic search
- `detected_events` / `event_items` — Derived candidate events for Hermes/agent handoff
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
| `GET /semantic?q=` | Semantic search (requires configured embedding provider) |

All endpoints (except `/login` and `/app`) require `x-brain-key` header, `key` query param, or `brain_key` cookie.

## Optional Email Digest

Daily email with top 5 tweets + top 5 links, scored by relevance. Configurable focus areas (default: AI, stablecoins, crypto, GitHub repos, product launches). Requires a [Resend](https://resend.com) API key.

If Hermes is handling Telegram/Slack delivery, leave `RESEND_API_KEY` unset and use the event detector handoff instead.

## License

MIT
