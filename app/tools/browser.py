"""Real-browser tools (Playwright + headless Chromium).

Model: one browser session per process, driven from a dedicated worker thread (Playwright's sync
API is bound to the thread that created it, and LangGraph runs tool calls on arbitrary executor
threads). Pages are described as a numbered list of interactive elements; the model acts by number.

Tools: browser_open, browser_snapshot, browser_click, browser_type, browser_select, browser_scroll,
browser_back, browser_screenshot, browser_close. Screenshots are saved to the workspace and attached
to the chat via the artifacts FILE_MARK.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable

from langchain_core.tools import tool

from app.tools.artifacts import out_dir, saved

log = logging.getLogger("nikki.browser")

IDLE_TIMEOUT_S = 15 * 60
MAX_TEXT = 12_000
NAV_TIMEOUT_MS = 30_000

_INTERACTIVE = (
    "a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=link], "
    "[role=tab], [role=menuitem], [role=checkbox], [role=radio], [contenteditable=true], summary"
)

_SNAPSHOT_JS = f"""
() => {{
  const vis = el => {{ const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; }};
  const els = Array.from(document.querySelectorAll({_INTERACTIVE!r})).filter(vis);
  const items = [];
  els.forEach((el, i) => {{
    el.setAttribute('data-nikki-ref', String(i + 1));
    const tag = el.tagName.toLowerCase();
    const type = el.getAttribute('type') || '';
    const role = el.getAttribute('role') || '';
    let label = (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || el.getAttribute('title') || el.getAttribute('name') || el.alt || '').trim().replace(/\\s+/g, ' ');
    if (tag === 'input' && el.labels && el.labels.length) label = (el.labels[0].innerText || '').trim() + (label ? ' [' + label + ']' : '');
    if (tag === 'select') label += ' options=' + Array.from(el.options).slice(0, 12).map(o => o.text.trim()).join('/');
    if (type === 'checkbox' || type === 'radio') label += el.checked ? ' (checked)' : ' (unchecked)';
    const href = tag === 'a' ? (el.getAttribute('href') || '') : '';
    items.push(`[${{i + 1}}] <${{tag}}${{type ? ' ' + type : ''}}${{role ? ' role=' + role : ''}}> ${{label.slice(0, 90)}}${{href && href.length < 80 ? ' -> ' + href : ''}}`);
  }});
  const text = (document.body ? document.body.innerText : '').replace(/\\n{{3,}}/g, '\\n\\n');
  return {{ title: document.title, url: location.href, items, text, scrollY: window.scrollY,
           height: document.documentElement.scrollHeight, viewport: window.innerHeight }};
}}
"""


class _Worker:
    """Owns the Playwright objects; every operation is a callable executed on this thread."""

    def __init__(self) -> None:
        self.q: queue.Queue[tuple[Callable[[], Any], queue.Queue]] = queue.Queue()
        self.pw = self.browser = self.context = self.page = None
        self.last_used = time.time()
        self.thread = threading.Thread(target=self._loop, name="nikki-browser", daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        while True:
            try:
                fn, reply = self.q.get(timeout=30)
            except queue.Empty:
                if self.browser and time.time() - self.last_used > IDLE_TIMEOUT_S:
                    self._close()
                continue
            try:
                reply.put((True, fn()))
            except Exception as e:  # noqa: BLE001
                reply.put((False, e))
            self.last_used = time.time()

    def call(self, fn: Callable[[], Any], timeout: float = 90) -> Any:
        reply: queue.Queue = queue.Queue()
        self.q.put((fn, reply))
        ok, val = reply.get(timeout=timeout)
        if not ok:
            raise val
        return val

    # ----- run on the worker thread
    def _ensure(self):
        if self.page and not self.page.is_closed():
            return self.page
        from playwright.sync_api import sync_playwright

        if self.pw is None:
            self.pw = sync_playwright().start()
        if self.browser is None or not self.browser.is_connected():
            self.browser = self.pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
            self.context = self.browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
            )
            self.context.set_default_timeout(NAV_TIMEOUT_MS)
            self.context.on("page", self._adopt_page)
        self.page = self.context.new_page()
        return self.page

    def _adopt_page(self, page) -> None:
        # follow target=_blank navigations
        self.page = page

    def _close(self) -> None:
        for obj in (self.context, self.browser):
            try:
                if obj:
                    obj.close()
            except Exception:  # noqa: BLE001
                pass
        self.context = self.browser = self.page = None


_worker: _Worker | None = None
_lock = threading.Lock()


def _w() -> _Worker:
    global _worker
    with _lock:
        if _worker is None:
            _worker = _Worker()
        return _worker


def _snapshot(page, full_text: bool) -> str:
    page.wait_for_load_state("domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=4000)
    except Exception:  # noqa: BLE001
        pass
    d = page.evaluate(_SNAPSHOT_JS)
    head = f"{d['title']}\n{d['url']}\nscroll {d['scrollY']}/{max(d['height'] - d['viewport'], 0)}px\n"
    items = "\n".join(d["items"][:150])
    if len(d["items"]) > 150:
        items += f"\n... {len(d['items']) - 150} more elements (scroll or be more specific)"
    text = d["text"]
    limit = MAX_TEXT if full_text else 3500
    if len(text) > limit:
        text = text[:limit] + f"\n[... {len(text) - limit} more chars; browser_snapshot(full_text=True) for more]"
    return f"{head}\n## Interactive elements (act by number)\n{items or '(none)'}\n\n## Page text\n{text}"


def _ref(page, n: int):
    loc = page.locator(f"[data-nikki-ref='{n}']")
    if loc.count() == 0:
        raise ValueError(f"element [{n}] not found — take a fresh browser_snapshot; numbers change after navigation")
    return loc.first


def _run(fn: Callable[[Any], Any], timeout: float = 90) -> str:
    try:
        w = _w()
        return w.call(lambda: fn(w._ensure()), timeout=timeout)
    except Exception as e:  # noqa: BLE001 — errors go back to the model
        msg = str(e).split("\n")[0][:400]
        return f"browser error: {type(e).__name__}: {msg}"


# ---------------------------------------------------------------- tools
@tool
def browser_open(url: str) -> str:
    """Open a web page in a real headless browser (JavaScript runs, logins and forms work) and return a
    snapshot: numbered interactive elements plus page text. Use for sites that need clicking, logging in,
    or filling forms; for plain reading, http_fetch/web_search are cheaper. The session (cookies, tabs)
    persists across calls for 15 minutes."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    def go(page):
        page.goto(url, wait_until="domcontentloaded")
        return _snapshot(page, False)

    return _run(go)


@tool
def browser_snapshot(full_text: bool = False) -> str:
    """Re-read the current page: numbered interactive elements and text. Call after any action that
    changes the page. full_text=True returns up to 12k chars of text."""
    return _run(lambda page: _snapshot(page, full_text))


@tool
def browser_click(element: int) -> str:
    """Click interactive element number `element` from the latest snapshot, then return a fresh snapshot."""

    def act(page):
        loc = _ref(page, element)
        try:
            with page.expect_navigation(timeout=5000, wait_until="domcontentloaded"):
                loc.click()
        except Exception:  # noqa: BLE001 — no navigation happened; that's fine
            pass
        time.sleep(0.6)
        return _snapshot(page, False)

    return _run(act)


@tool
def browser_type(element: int, text: str, press_enter: bool = False, clear: bool = True) -> str:
    """Type `text` into input element number `element` (clears it first unless clear=False);
    press_enter=True submits. Returns a fresh snapshot."""

    def act(page):
        loc = _ref(page, element)
        loc.click()
        if clear:
            loc.fill("")
        loc.type(text, delay=15)
        if press_enter:
            try:
                with page.expect_navigation(timeout=5000, wait_until="domcontentloaded"):
                    loc.press("Enter")
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.5)
        return _snapshot(page, False)

    return _run(act)


@tool
def browser_select(element: int, option: str) -> str:
    """Choose `option` (visible text or value) in <select> element number `element`."""

    def act(page):
        loc = _ref(page, element)
        try:
            loc.select_option(label=option)
        except Exception:  # noqa: BLE001
            loc.select_option(value=option)
        return _snapshot(page, False)

    return _run(act)


@tool
def browser_scroll(direction: str = "down", pages: float = 1) -> str:
    """Scroll the page 'down' or 'up' by `pages` viewport heights (or 'top'/'bottom') and re-snapshot."""

    def act(page):
        if direction == "top":
            page.evaluate("window.scrollTo(0,0)")
        elif direction == "bottom":
            page.evaluate("window.scrollTo(0,document.documentElement.scrollHeight)")
        else:
            sign = -1 if direction == "up" else 1
            page.evaluate(f"window.scrollBy(0,{sign * pages}*window.innerHeight)")
        time.sleep(0.4)
        return _snapshot(page, False)

    return _run(act)


@tool
def browser_back() -> str:
    """Go back one page in the browser history."""

    def act(page):
        page.go_back(wait_until="domcontentloaded")
        return _snapshot(page, False)

    return _run(act)


@tool
def browser_screenshot(full_page: bool = False) -> str:
    """Take a screenshot of the current page (saved to the workspace and shown to the user). Use it to
    show the user what you see or when the text snapshot is not enough (charts, layouts)."""

    def act(page):
        p = out_dir("screenshots") / f"{time.strftime('%Y%m%d-%H%M%S')}-{(page.title() or 'page')[:30].replace('/', '-')}.png"
        page.screenshot(path=str(p), full_page=full_page)
        return saved(p, page.url[:120])

    return _run(act)


@tool
def browser_close() -> str:
    """Close the browser session (drops cookies/logins). Do this when a task involving a login is done."""
    try:
        w = _w()
        w.call(w._close)
        return "browser closed"
    except Exception as e:  # noqa: BLE001
        return f"browser error: {e}"


for _t in (browser_open, browser_snapshot, browser_scroll, browser_back, browser_screenshot, browser_close):
    _t.metadata = {"requires_approval": False}
for _t in (browser_click, browser_type, browser_select):
    _t.metadata = {"requires_approval": False}  # navigation-level actions; purchases/sends still need the user's say-so via prompt rules

TOOLS = [browser_open, browser_snapshot, browser_click, browser_type, browser_select, browser_scroll, browser_back, browser_screenshot, browser_close]
