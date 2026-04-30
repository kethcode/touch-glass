# Touch Glass Deployment Runbook

Target: Ubuntu 24.04 server, CPU-only mini server, local llama.cpp embeddings, Hermes Agent handles Telegram/Slack delivery.

Assumptions:
- Repo path: `/opt/touch-glass`
- Linux user: `touchglass`
- API port: `8888`
- llama.cpp embedding port: `8080`
- Chrome CDP port: `9222`
- Chrome runs under `Xvfb` on display `:99`; CDP stays bound to `127.0.0.1`.
- You will do the first Chrome login through a temporary VNC tunnel into the virtual display.

Adjust paths/users if you deploy under your normal account instead.

## 1. System Packages

```bash
sudo apt update
sudo apt install -y \
  build-essential cmake git curl libcurl4-openssl-dev \
  python3 python3-venv python3-pip \
  xvfb x11vnc
```

Install Chrome from Google if it is not already present:

```bash
if ! command -v google-chrome >/dev/null 2>&1; then
  wget -q -O /tmp/google-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  sudo apt install -y /tmp/google-chrome.deb
fi
```

## 2. User And Repo

```bash
id -u touchglass >/dev/null 2>&1 || sudo useradd --system --create-home --shell /bin/bash touchglass
sudo mkdir -p /opt
sudo chown touchglass:touchglass /opt

sudo -iu touchglass
cd /opt
git clone https://github.com/kethcode/touch-glass.git
cd /opt/touch-glass
```

If you need SSH auth for the private fork, set that up for the `touchglass` user first, or clone over HTTPS with your preferred credential helper.

Create Python env:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
```

Configure Touch Glass:

```bash
cp .env.example .env
nano .env
nano topics/index.json
```

Minimum `.env` values:

```bash
BRAIN_PASSWORD=replace-with-long-password
# Optional: set this before first run if the DB should live off the repo path.
# BRAIN_DB=/opt/touch-glass/brain.db
CDP_URL=http://127.0.0.1:9222
TWITTER_USERNAME=your_username
EMBEDDING_PROVIDER=llamacpp
EMBEDDING_BASE_URL=http://127.0.0.1:8080/v1
EMBEDDING_MODEL=Qwen3-Embedding-0.6B-Q8_0
EMBEDDING_DIMS=native
TOUCH_GLASS_TOPICS_CONFIG=topics/index.json
TOUCH_GLASS_HERMES_CONSOLIDATION_PROMPT=prompts/hermes_consolidation.md
TOUCH_GLASS_HERMES_COMMENTARY_PROMPT=prompts/hermes_commentary.md
TOUCH_GLASS_HERMES_MARK_DELIVERED=true
TOUCH_GLASS_HERMES_LIMIT=25
TOUCH_GLASS_HERMES_WINDOW_MINUTES=120
# Optional final handoff gates:
# TOUCH_GLASS_HERMES_MIN_PRIORITY=7
# TOUCH_GLASS_HERMES_MIN_SCORE=20
# For uv-managed Hermes wrapper runs:
# TOUCH_GLASS_PYTHON_CMD="uv run python"
```

Leave `RESEND_API_KEY` unset if Hermes handles delivery.

## 3. Build llama.cpp

```bash
sudo -iu touchglass
mkdir -p ~/src
git clone https://github.com/ggml-org/llama.cpp.git ~/src/llama.cpp
cd ~/src/llama.cpp
cmake -B build -DGGML_NATIVE=ON -DGGML_CURL=ON -DLLAMA_CURL=ON
cmake --build build --config Release -j
```

Start manually once to download the model and verify the endpoint:

```bash
~/src/llama.cpp/build/bin/llama-server \
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

In another shell:

```bash
curl -s http://127.0.0.1:8080/v1/embeddings \
  -H 'content-type: application/json' \
  -d '{"model":"Qwen3-Embedding-0.6B-Q8_0","input":["hello world"]}' \
  | python3 -m json.tool | head
```

Stop `llama-server` with `Ctrl+C` after this check.

## 4. Headless Chrome CDP Login

On a server install, run Chrome as a normal graphical browser inside a virtual
display. This is usually more reliable for logged-in social sites than
`--headless=new`.

If Hermes already installed Chrome for Testing and you prefer that binary, you
can swap the Chrome `ExecStart` path below. Keep the same CDP flags and the
same `--user-data-dir`.

Hermes browser tools can help with setup, but keep the scrape browser dedicated
to Touch Glass:
- Do not share Hermes's normal browser profile with Touch Glass.
- Touch Glass needs a long-lived authenticated browser profile at
  `/home/touchglass/ChromeDebug`.
- If Hermes can drive an existing CDP endpoint, you can point it at
  `http://127.0.0.1:9222` for login checks or debugging.
- If Hermes installed a working Chrome-for-Testing binary, you can use that
  binary in `touch-glass-chrome.service`; keep the service-owned profile and
  CDP flags unchanged.
- If browser-tool login gets awkward because of MFA/captcha, use the VNC path
  below for the first login and let Touch Glass keep the session afterward.

## 5. systemd Services

These are system services that run as `touchglass`.

### Virtual display

Create `/etc/systemd/system/touch-glass-xvfb.service`:

```ini
[Unit]
Description=Touch Glass virtual display
After=network-online.target

[Service]
Type=simple
User=touchglass
ExecStart=/usr/bin/Xvfb :99 -screen 0 1440x900x24 -ac
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Chrome CDP

Create `/etc/systemd/system/touch-glass-chrome.service`:

```ini
[Unit]
Description=Touch Glass Chrome CDP browser
After=touch-glass-xvfb.service
Requires=touch-glass-xvfb.service

[Service]
Type=simple
User=touchglass
Environment=DISPLAY=:99
ExecStart=/usr/bin/google-chrome --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir=/home/touchglass/ChromeDebug --no-first-run --no-default-browser-check --disable-dev-shm-usage --password-store=basic --remote-allow-origins=*
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Start Chrome for the first login:

```bash
sudo systemctl daemon-reload
sudo systemctl start touch-glass-xvfb
sudo systemctl start touch-glass-chrome
```

Expose the virtual display over a local SSH tunnel.

On the server:

```bash
sudo -iu touchglass
x11vnc -display :99 -localhost -forever -nopw
```

On your Mac:

```bash
ssh -L 5900:127.0.0.1:5900 YOUR_SSH_USER@SERVER_HOST
open vnc://127.0.0.1:5900
```

Use the VNC window to interact with Chrome.

Log into:
- `https://x.com`
- `https://linkedin.com` if you plan to scrape LinkedIn
- `https://web.telegram.org/a/` if you plan to scrape Telegram Web

Keep this Chrome profile. Check CDP:

```bash
curl -s http://127.0.0.1:9222/json/version | python3 -m json.tool
```

### llama embeddings

Create `/etc/systemd/system/touch-glass-llama.service`:

```ini
[Unit]
Description=Touch Glass llama.cpp embedding server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=touchglass
WorkingDirectory=/home/touchglass/src/llama.cpp
ExecStart=/home/touchglass/src/llama.cpp/build/bin/llama-server --hf-repo Qwen/Qwen3-Embedding-0.6B-GGUF --hf-file Qwen3-Embedding-0.6B-Q8_0.gguf --embedding --pooling last --host 127.0.0.1 --port 8080 -c 2048 -ub 8192 --threads 16
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### API

Create `/etc/systemd/system/touch-glass-api.service`:

```ini
[Unit]
Description=Touch Glass API
After=network-online.target touch-glass-llama.service
Wants=network-online.target touch-glass-llama.service

[Service]
Type=simple
User=touchglass
WorkingDirectory=/opt/touch-glass
EnvironmentFile=/opt/touch-glass/.env
ExecStart=/opt/touch-glass/.venv/bin/python -m uvicorn api.server:app --host 127.0.0.1 --port 8888
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Monitor

Create `/etc/systemd/system/touch-glass-monitor.service`:

```ini
[Unit]
Description=Touch Glass scraper monitor
After=network-online.target touch-glass-chrome.service touch-glass-api.service touch-glass-llama.service
Wants=network-online.target touch-glass-chrome.service touch-glass-api.service touch-glass-llama.service

[Service]
Type=simple
User=touchglass
WorkingDirectory=/opt/touch-glass
EnvironmentFile=/opt/touch-glass/.env
ExecStart=/opt/touch-glass/.venv/bin/python monitor.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now touch-glass-xvfb
sudo systemctl enable --now touch-glass-chrome
sudo systemctl enable --now touch-glass-llama
sudo systemctl enable --now touch-glass-api
sudo systemctl enable --now touch-glass-monitor
```

The monitor expects Chrome CDP to already be reachable on `127.0.0.1:9222`.
Do the first login before expecting useful scrape output.

Logs:

```bash
journalctl -u touch-glass-xvfb -f
journalctl -u touch-glass-chrome -f
journalctl -u touch-glass-llama -f
journalctl -u touch-glass-api -f
journalctl -u touch-glass-monitor -f
```

## 6. First Validation

API auth:

```bash
set -a
source /opt/touch-glass/.env
set +a

curl -s "http://127.0.0.1:8888/stats?key=${BRAIN_PASSWORD}" | python3 -m json.tool
curl -s "http://127.0.0.1:8888/timeline?key=${BRAIN_PASSWORD}&limit=5" | python3 -m json.tool
```

Embedding config:

```bash
cd /opt/touch-glass
set -a && source .env && set +a
.venv/bin/python enrichment/embeddings.py status
```

Event detector:

```bash
cd /opt/touch-glass
set -a && source .env && set +a
.venv/bin/python -m enrichment.events detect --config topics/index.json --format markdown
```

Expected early output is often `[SILENT]` until enough matching posts exist.

## 7. Hermes Cron Handoff

Once Hermes gateway is already configured for Telegram/Slack, tune the prompt:

```bash
cd /opt/touch-glass
nano prompts/hermes_commentary.md
```

Test the wrapper:

```bash
cd /opt/touch-glass
scripts/hermes_event_briefing.sh
```

If manual output looks useful, create a cron job from the Hermes chat:

```text
/cron add "every 15m" "Run `/opt/touch-glass/scripts/hermes_event_briefing.sh`. If the output is [SILENT], respond exactly [SILENT]. Otherwise follow the instructions in the output and send the briefing to Telegram."
```

Notes:
- The wrapper marks delivered events when `TOUCH_GLASS_HERMES_MARK_DELIVERED=true`.
- If Hermes fails after reading output but before delivery, you may miss one alert. For first pass this is acceptable. If it becomes a problem, set `TOUCH_GLASS_HERMES_MARK_DELIVERED=false` and mark delivered from a separate acknowledged path.
- Keep Touch Glass bound to `127.0.0.1` unless you intentionally expose it behind a tunnel or reverse proxy.

## 8. Common Troubleshooting

Chrome/CDP unreachable:

```bash
curl -s http://127.0.0.1:9222/json/version
```

If that fails, restart Chrome manually under the `touchglass` user.

No tweets scraped:
- Confirm the debug Chrome window is logged into X.
- Check `journalctl -u touch-glass-monitor -f`.
- Run a one-off scraper manually:

```bash
cd /opt/touch-glass
set -a && source .env && set +a
.venv/bin/python scrapers/timeline.py timeline
```

Embedding server unreachable:

```bash
curl -s http://127.0.0.1:8080/health || true
journalctl -u touch-glass-llama -n 100
```

Hermes sends noisy reports:
- Raise topic `min_score`.
- Add `meme_terms`.
- Add official accounts/domains so real source diversity wins.
- Lower cron frequency until the detector is tuned.
