# Touch Glass Project Context

This file is a handoff note for future sessions. It summarizes the repo, the
current deployment goal, the recent changes, and the practical constraints that
matter when continuing work.

## High-Level Intent

Touch Glass is a self-hosted social feed monitor. It connects to an already
authenticated Chrome session via CDP, scrapes social feeds, stores the results in
SQLite, enriches links, and exposes a small FastAPI/search UI.

The immediate project direction is narrower than the full original app:

- Use X/Twitter as the main feed source.
- Gather feed content at intervals.
- Store recent items in SQLite as a cheap local event store.
- Detect topic/event clusters with deterministic scoring.
- Hand candidate events to Hermes Agent on a cron schedule.
- Let Hermes write commentary and deliver through Telegram or Slack.

The intended first pass is temporal event detection, not long-term archival or
deep analytics. A week or two of data is enough for this phase. The deployment
can start with a clean database; no old embeddings need to be preserved.

Target pipeline:

```text
x/twitter feed
  -> cheap harvester
  -> sqlite/event store
  -> topic detector
  -> hermes cron
  -> commentary agent
  -> slack or telegram digest
```

## Deployment Target

Current target machine:

- Ubuntu 24.04 server install
- Ryzen 9 9955HX
- 96 GB RAM
- 1 TB PCIe 4 OS drive
- 2 TB PCIe 4 data drive
- No GPU

Development is happening on a macOS ARM64 MacBook, but the target server is
Linux AMD64. Avoid adding macOS-only runtime assumptions. The Python app talks
to local HTTP services, so model-serving and browser details should stay outside
the core application where possible.

There may later be a larger GPU machine available, but the current plan is to
keep the mini server deployment simple and CPU-only.

## Current Architecture

Important files:

- `monitor.py` is the always-on orchestrator. It runs scrape and enrichment
  tasks every 10 minutes.
- `scrapers/cdp.py` connects to Chrome over CDP. The deployment expects Chrome
  to be started separately and kept alive.
- `scrapers/timeline.py` handles X timeline/bookmarks/own tweets.
- `scrapers/followers.py` handles follower/following/mutual detection.
- `scrapers/linkedin.py` and `scrapers/telegram.py` exist from the original app.
  They are not the center of the current deployment plan.
- `enrichment/links.py` fetches and enriches links.
- `enrichment/embeddings.py` supports optional semantic embeddings through
  OpenAI or a local OpenAI-compatible endpoint such as llama.cpp.
- `enrichment/events.py` is the deterministic topic/event detector added for
  Hermes handoff.
- `enrichment/digest.py` is the original optional Resend email digest path.
  For this deployment, leave Resend unset and use Hermes instead.
- `db/schema.py` defines the SQLite schema and upsert helpers.
- `api/server.py` provides the web UI and search/timeline API.
- `DEPLOYMENT.md` is the Ubuntu 24.04 runbook.
- `topics/index.json` is the active grouped event detection config.

SQLite defaults to `brain.db` in the repo root. For server deployment, consider
setting `BRAIN_DB` to a path on the data drive before first run if the database
should not live under `/opt/touch-glass`.

## Browser/CDP Model

Touch Glass should use a dedicated long-lived Chrome profile. Do not share
Hermes Agent's normal browser profile with Touch Glass.

Recommended server model:

- Run `Xvfb` on display `:99`.
- Run Chrome as the `touchglass` user.
- Bind CDP to `127.0.0.1:9222`.
- Use a dedicated profile such as `/home/touchglass/ChromeDebug`.
- Do first login through VNC over SSH, or use Hermes browser tools if they can
  drive the existing CDP endpoint cleanly.

Hermes browser tools can help with login checks/debugging, but the scrape
browser should be service-owned and persistent. If Hermes installed Chrome for
Testing, that binary can be used in the Chrome systemd service as long as the
profile path and CDP flags remain unchanged.

## Embeddings Direction

The repo now supports three embedding provider modes:

- `EMBEDDING_PROVIDER=disabled`
- `EMBEDDING_PROVIDER=openai`
- `EMBEDDING_PROVIDER=llamacpp`

`auto` enables OpenAI only when `OPENAI_API_KEY` is set; otherwise embeddings are
disabled. For this deployment, prefer local CPU embeddings:

```bash
EMBEDDING_PROVIDER=llamacpp
EMBEDDING_BASE_URL=http://127.0.0.1:8080/v1
EMBEDDING_MODEL=Qwen3-Embedding-0.6B-Q8_0
EMBEDDING_DIMS=native
EMBEDDING_MAX_CHARS=6000
```

The current recommendation is llama.cpp serving
`Qwen3-Embedding-0.6B-Q8_0.gguf` through `llama-server`:

```bash
llama-server \
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

Why llama.cpp for now:

- It is the simplest CPU-only path.
- It has a small operational surface.
- It avoids GPU/runtime complexity on the mini server.
- It exposes an OpenAI-compatible endpoint that the Python app can use.

vLLM or SGLang may make sense later on a GPU box, especially for high-throughput
generation or concurrent serving. They are not the right default for this
CPU-only mini server.

## Event Detection

`enrichment/events.py` turns recent tweets into candidate event bundles. It is
intentionally deterministic and designed to feed an LLM/commentary agent, not to
be the commentary agent itself.

Basic command:

```bash
python -m enrichment.events detect --config topics/index.json --format markdown
```

Hermes cron command shape:

```bash
python -m enrichment.events detect --config topics/index.json --format markdown --mark-delivered
```

If nothing crosses threshold, output is exactly:

```text
[SILENT]
```

Hermes should suppress delivery when the detector returns `[SILENT]`.

Scoring currently emphasizes source diversity over raw tweet count:

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

There is also a small capped quality/engagement bump based on the top matching
items. The example topic config supports:

- `keywords`
- `domains`
- `accounts`
- `official_accounts`
- `official_domains`
- `security_terms`
- `meme_terms`
- per-topic `window_minutes`, `min_items`, `min_score`, and `max_items`

Default/example topics cover AI agents/dev tools, AI model releases,
stablecoins/payments, and crypto launches/infra.

## Hermes Integration

Hermes is expected to handle:

- Scheduling via cron.
- Running the detector command.
- Calling a larger commentary model if desired.
- Sending Telegram or Slack output through its configured messaging tools.

The current integration is intentionally just a CLI handoff. There is no MCP
server in front of Touch Glass yet. Add MCP only if Hermes needs structured
query tools beyond "run detector and summarize output".

First-pass Hermes prompt from `DEPLOYMENT.md`:

```text
/cron add "every 15m" "Run `cd /opt/touch-glass && set -a && source .env && set +a && .venv/bin/python -m enrichment.events detect --config topics/index.json --format markdown --mark-delivered`. If the output is [SILENT], respond exactly [SILENT]. Otherwise, write a concise Telegram briefing about the candidate events."
```

Known delivery caveat: with `--mark-delivered`, events are marked delivered
after printing. If Hermes reads output and then fails before sending, one alert
could be missed. This is acceptable for the first pass. If it becomes a problem,
remove `--mark-delivered` and add an explicit acknowledged-delivery path.

## Database State And Schema Notes

The deployment can start clean. `init_db()` creates the current schema.

Important tables:

- `tweets`
- `contacts`
- `links`
- `tweet_links`
- `scrape_log`
- `embeddings`
- `messages`
- `conversations`
- `detected_events`
- `event_items`

FTS5 tables exist for tweets, links, contacts, and messages.

The Telegram tables were added because scrapers/API expected them but they were
missing from the earlier schema. This matters even if Telegram scraping is not
central to the current plan.

Embedding rows store a model key like:

```text
llamacpp:Qwen3-Embedding-0.6B-Q8_0:dims=native
```

Semantic search compares only embeddings from the active model key. On this
clean install, there is no migration concern.

## Current Operational Plan

Follow `DEPLOYMENT.md` on the Ubuntu server:

1. Install system packages.
2. Create `touchglass` user and clone repo to `/opt/touch-glass`.
3. Create Python venv and install `requirements.txt`.
4. Copy `.env.example` to `.env`; tune grouped topic files under `topics/`.
5. Build llama.cpp.
6. Verify the local embedding endpoint.
7. Start Xvfb and Chrome CDP.
8. Log into X in the Chrome debug profile.
9. Start systemd services.
10. Validate API, embedding status, and event detector.
11. Add Hermes cron handoff.

Keep services bound to localhost unless intentionally putting them behind a
tunnel or reverse proxy.

## Verification Commands

Local development checks:

```bash
python3 -m compileall -q .
BRAIN_DB=/tmp/touch-glass-check.db python3 -m enrichment.events detect --format markdown
git diff --check
```

Expected detector output on an empty DB:

```text
[SILENT]
```

Embedding status:

```bash
python enrichment/embeddings.py status
```

Server checks:

```bash
curl -s http://127.0.0.1:9222/json/version | python3 -m json.tool
curl -s http://127.0.0.1:8080/v1/embeddings \
  -H 'content-type: application/json' \
  -d '{"model":"Qwen3-Embedding-0.6B-Q8_0","input":["hello world"]}' \
  | python3 -m json.tool | head
curl -s "http://127.0.0.1:8888/stats?key=${BRAIN_PASSWORD}" | python3 -m json.tool
```

Systemd logs:

```bash
journalctl -u touch-glass-xvfb -f
journalctl -u touch-glass-chrome -f
journalctl -u touch-glass-llama -f
journalctl -u touch-glass-api -f
journalctl -u touch-glass-monitor -f
```

## Known Risks And Open Questions

- X/Twitter scraping is DOM-dependent. Selectors can break when X changes its
  UI. The first server run should validate actual scrape output, not just CDP
  connectivity.
- Login durability depends on keeping the Chrome profile intact.
- The monitor still runs LinkedIn, Telegram, followers, following, embeddings,
  and optional digest tasks on its existing cadence. If the first deployment
  should be X-only and lighter-weight, add env flags to disable unused tasks.
- The original daily digest still runs once per day, but sends no email unless
  `RESEND_API_KEY` and `DIGEST_EMAIL` are configured. This is acceptable for now
  but could be made explicitly configurable later.
- Event detection is deterministic but early thresholds may need tuning once
  real feed data arrives.
- Topic clustering is currently rule/scoring based. Embeddings are available for
  semantic search and could later support clustering, but that is not part of
  the first pass.
- If Hermes needs richer access than a detector CLI, add a small MCP server or
  HTTP endpoint later. Do not start there unless the CLI handoff proves too
  narrow.

## Git/Collaboration Preference

The user prefers to review, stage, commit, and push manually. Future agents
should feel free to inspect git state and edit the worktree, but should not run
`git add`, `git commit`, `git push`, branch changes, or destructive git commands
unless explicitly asked.

Read-only git commands such as `git status`, `git diff`, `git log`, and
`git show` are fine.
