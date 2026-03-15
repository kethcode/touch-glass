"""
Browser automation via Playwright CDP connection.

Connects to a running Chrome instance via CDP (localhost:9222),
or launches a persistent Playwright browser with saved sessions.
"""

import json
import os
import re
import time
import atexit

# Global browser/page state
_browser = None
_context = None
_pages = {}  # url_pattern -> Page

CDP_URL = os.environ.get("CDP_URL", "http://localhost:9222")
SESSION_DIR = os.path.join(os.path.dirname(__file__), "..", "chrome-session")


def _get_browser():
    """Connect to Chrome via CDP, or launch a persistent browser."""
    global _browser, _context
    if _browser and _browser.is_connected():
        return _browser, _context

    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()

    # Try CDP connection to existing Chrome first
    try:
        _browser = pw.chromium.connect_over_cdp(CDP_URL)
        _context = _browser.contexts[0] if _browser.contexts else _browser.new_context()
        print(f"[cdp] Connected to Chrome via CDP at {CDP_URL}")
        return _browser, _context
    except Exception as e:
        print(f"[cdp] CDP connection failed ({e}), launching persistent browser...")

    # Fallback: launch persistent browser with saved session
    os.makedirs(SESSION_DIR, exist_ok=True)
    _context = pw.chromium.launch_persistent_context(
        SESSION_DIR,
        headless=False,
        viewport={"width": 1440, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )
    _browser = _context.browser
    print(f"[cdp] Launched persistent browser (session: {SESSION_DIR})")
    return _browser, _context


def _cleanup():
    global _browser, _context
    try:
        if _context:
            _context.close()
        if _browser:
            _browser.close()
    except Exception:
        pass

atexit.register(_cleanup)


def get_page(url_pattern: str):
    """Get or find a page matching URL pattern."""
    _, context = _get_browser()

    # Check existing pages
    for page in context.pages:
        if url_pattern in page.url:
            return page

    # No matching page, use first page or create new
    if context.pages:
        return context.pages[0]
    return context.new_page()


def navigate(url: str, wait_until: str = "domcontentloaded"):
    """Navigate to a URL, return the page."""
    _, context = _get_browser()
    page = context.pages[0] if context.pages else context.new_page()
    if not url.startswith("http"):
        url = "https://" + url
    page.goto(url, wait_until=wait_until, timeout=30000)
    return page


def evaluate(page, expression: str, timeout: int = 15000):
    """Evaluate JavaScript on a page."""
    return page.evaluate(expression)


def evaluate_json(page, expression: str):
    """Evaluate JS and return parsed result."""
    result = page.evaluate(expression)
    if isinstance(result, str):
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return result
    return result


def scroll_down(page, pixels: int = 1400):
    """Scroll down."""
    page.evaluate(f"window.scrollBy(0, {pixels})")


def scroll_to_top(page):
    """Scroll to top."""
    page.evaluate("window.scrollTo(0, 0)")


def ensure_page(url: str, wait_time: float = 3.0):
    """Ensure a page is loaded with the given URL."""
    _, context = _get_browser()

    # Look for existing page
    for page in context.pages:
        if url.replace("https://", "").replace("http://", "") in page.url:
            return page

    # Navigate
    page = navigate(url)
    time.sleep(wait_time)
    return page


def wait(seconds: float = 1.5):
    """Simple wait."""
    time.sleep(seconds)


# --- Tweet extraction JS snippets ---

EXTRACT_TWEETS_JS = """
() => {
    return [...document.querySelectorAll('article[data-testid="tweet"]')].map(t => {
        const authorEl = t.querySelector('[data-testid="User-Name"]');
        const authorParts = authorEl ? authorEl.innerText.split('\\n') : [];
        const usernameMatch = authorParts.find(p => p.startsWith('@'));
        const displayName = authorParts[0] || '';

        const parseCount = (s) => {
            if (!s) return 0;
            s = s.replace(/,/g, '');
            if (s.endsWith('K')) return Math.round(parseFloat(s) * 1000);
            if (s.endsWith('M')) return Math.round(parseFloat(s) * 1000000);
            return parseInt(s) || 0;
        };

        const linkEl = t.querySelector('a[href*="/status/"]');
        const tweetUrl = linkEl ? 'https://x.com' + linkEl.getAttribute('href') : null;
        const statusMatch = tweetUrl ? tweetUrl.match(/\\/status\\/(\\d+)/) : null;
        const tweetId = statusMatch ? statusMatch[1] : null;

        const tweetTextEl = t.querySelector('[data-testid="tweetText"]');
        const links = [];
        if (tweetTextEl) {
            tweetTextEl.querySelectorAll('a[href]').forEach(a => {
                const href = a.getAttribute('href');
                if (href && !href.includes('/status/') && !href.startsWith('/')) {
                    links.push(href.startsWith('http') ? href : 'https://x.com' + href);
                }
            });
        }

        const timeEl = t.querySelector('time');
        const timestamp = timeEl ? timeEl.getAttribute('datetime') : null;

        return {
            id: tweetId,
            author_username: usernameMatch ? usernameMatch.replace('@', '') : null,
            author_display_name: displayName,
            text: tweetTextEl ? tweetTextEl.innerText : '',
            likes: parseCount(t.querySelector('[data-testid="like"] span')?.innerText),
            retweets: parseCount(t.querySelector('[data-testid="retweet"] span')?.innerText),
            replies: parseCount(t.querySelector('[data-testid="reply"] span')?.innerText),
            views: parseCount(t.querySelector('[data-testid="app-text-transition-container"] span')?.innerText),
            url: tweetUrl,
            timestamp: timestamp,
            links: links,
        };
    }).filter(t => t.id);
}
"""

EXTRACT_USERS_JS = """
() => {
    return [...document.querySelectorAll('[data-testid="UserCell"]')].map(cell => {
        // X 2026: User-Name testid may not exist, fall back to full cell text parsing
        const nameEl = cell.querySelector('[data-testid="User-Name"]');
        let username = null, displayName = '';

        if (nameEl) {
            const parts = nameEl.innerText.split('\\n');
            const match = parts.find(p => p.startsWith('@'));
            username = match ? match.replace('@', '') : null;
            displayName = parts[0] || '';
        } else {
            // Fallback: parse from cell text
            const text = cell.innerText;
            const lines = text.split('\\n').map(l => l.trim()).filter(l => l);
            displayName = lines[0] || '';
            const match = lines.find(l => l.startsWith('@'));
            username = match ? match.replace('@', '') : null;
        }

        const bioEl = cell.querySelector('[data-testid="UserDescription"]');
        let bio = bioEl ? bioEl.innerText : '';
        if (!bio) {
            // Fallback: grab text after username line, skip "Following"/"Follow" buttons
            const text = cell.innerText;
            const lines = text.split('\\n').map(l => l.trim()).filter(l => l);
            const skipWords = ['Following', 'Follow', displayName, '@' + username];
            bio = lines.filter(l => !skipWords.includes(l) && l.length > 5).join(' ').slice(0, 200);
        }

        const imgEl = cell.querySelector('img[src*="profile_images"]');

        return {
            username: username,
            display_name: displayName,
            bio: bio,
            profile_image: imgEl ? imgEl.src : null,
        };
    }).filter(u => u.username);
}
"""
