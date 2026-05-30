from __future__ import annotations

import asyncio
import random
from typing import Any, cast

try:
    from playwright_stealth import stealth_async

    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

try:
    from camoufox.async_api import AsyncCamoufox

    HAS_CAMOUFOX = True
except ImportError:
    HAS_CAMOUFOX = False

try:
    import patchright.async_api as patchright_api  # type: ignore

    HAS_PATCHRIGHT = True
except ImportError:
    HAS_PATCHRIGHT = False

from playwright.async_api import BrowserContext, Page, ViewportSize, async_playwright

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
    {"width": 1280, "height": 800},
]

STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

Object.defineProperty(navigator, 'plugins', {
  get: () => {
    const arr = [
      { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
      { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
    ];
    arr.__proto__ = PluginArray.prototype;
    return arr;
  }
});

Object.defineProperty(navigator, 'languages', { get: () => ['es-CL', 'es', 'en-US', 'en'] });

if (!window.chrome) { window.chrome = { runtime: {} }; }

const _origQuery = window.navigator.permissions && window.navigator.permissions.query.bind(window.navigator.permissions);
if (_origQuery) {
  window.navigator.permissions.query = (p) =>
    p.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : _origQuery(p);
}

Object.defineProperty(navigator, 'userAgentData', {
  get: () => ({
    brands: [
      { brand: 'Chromium', version: '124' },
      { brand: 'Google Chrome', version: '124' },
      { brand: 'Not-A.Brand', version: '99' },
    ],
    mobile: false,
    platform: 'macOS',
  }),
});
"""


async def jitter_sleep(lo: float = 0.8, hi: float = 2.5) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


async def apply_stealth(page: Page) -> None:
    await page.add_init_script(STEALTH_INIT_SCRIPT)
    if HAS_STEALTH:
        await stealth_async(page)


async def launch_playwright(headed: bool = False) -> tuple[Any, BrowserContext, Page]:
    pw = await async_playwright().start()
    user_agent = random.choice(USER_AGENTS)
    viewport = cast(ViewportSize, random.choice(VIEWPORTS))
    browser = await pw.chromium.launch(
        headless=not headed,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--flag-switches-begin",
            "--disable-site-isolation-trials",
            "--flag-switches-end",
        ],
    )
    context = await browser.new_context(
        user_agent=user_agent,
        viewport=viewport,
        locale="es-CL",
        timezone_id="America/Santiago",
        color_scheme="light",
        extra_http_headers={
            "Accept-Language": "es-CL,es;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
        },
    )
    page = await context.new_page()
    await apply_stealth(page)
    return pw, context, page


async def launch_patchright(headed: bool = False) -> tuple[Any, BrowserContext, Page]:
    if not HAS_PATCHRIGHT:
        raise RuntimeError("patchright is not installed")
    pw = await patchright_api.async_playwright().start()
    user_agent = random.choice(USER_AGENTS)
    viewport = cast(ViewportSize, random.choice(VIEWPORTS))
    browser = await pw.chromium.launch(headless=not headed)
    context = await browser.new_context(
        user_agent=user_agent,
        viewport=viewport,
        locale="es-CL",
        timezone_id="America/Santiago",
        color_scheme="light",
        extra_http_headers={"Accept-Language": "es-CL,es;q=0.9"},
    )
    page = await context.new_page()
    return pw, context, page


async def launch_camoufox(headed: bool = False) -> tuple[Any, None, Page]:
    if not HAS_CAMOUFOX:
        raise RuntimeError("camoufox is not installed")
    from browserforge.fingerprints import Screen

    viewport = random.choice(VIEWPORTS)
    fox = AsyncCamoufox(
        headless=not headed,
        os="macos",
        screen=Screen(max_width=viewport["width"], max_height=viewport["height"]),
        locale=["es-CL", "es", "en-US"],
        geoip=True,
        fonts=["Arial", "Helvetica Neue"],
    )
    browser: Any = await fox.__aenter__()
    contexts = getattr(browser, "contexts", [])
    context = contexts[0] if contexts else await browser.new_context()
    page = await context.new_page()
    return fox, None, page


class ScraperEngine:
    def __init__(self, engine: str = "playwright", headed: bool = False) -> None:
        if engine not in {"playwright", "camoufox", "patchright"}:
            raise ValueError(f"Unknown engine: {engine!r}")
        self.engine = engine
        self.headed = headed
        self._pw: Any | None = None
        self._fox: Any | None = None
        self._context: BrowserContext | None = None
        self.page: Page

    async def __aenter__(self) -> "ScraperEngine":
        if self.engine == "camoufox":
            self._fox, _, self.page = await launch_camoufox(self.headed)
        elif self.engine == "patchright":
            self._pw, self._context, self.page = await launch_patchright(self.headed)
        else:
            self._pw, self._context, self.page = await launch_playwright(self.headed)
        return self

    async def __aexit__(self, *_: Any) -> None:
        try:
            if self.engine == "camoufox" and self._fox is not None:
                await self._fox.__aexit__(None, None, None)
            elif self._context is not None:
                await self._context.close()
                if self._pw is not None:
                    await self._pw.stop()
        except Exception:
            return

    async def warm_up(self, url: str) -> None:
        try:
            await self.page.goto(url, wait_until="networkidle", timeout=60_000)
        except Exception:
            pass
        await jitter_sleep(2.0, 4.0)
        await self.page.evaluate(
            "window.scrollBy(0, Math.floor(Math.random() * 300 + 100))"
        )
        await jitter_sleep(0.5, 1.5)

    async def goto_with_retry(
        self, url: str, *, wait_for: str | None = None, retries: int = 3
    ) -> bool:
        for attempt in range(1, retries + 1):
            try:
                await self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            except Exception:
                if attempt < retries:
                    await asyncio.sleep(attempt * 5.0)
                continue

            if wait_for:
                try:
                    await self.page.wait_for_selector(wait_for, timeout=30_000)
                    await jitter_sleep(0.8, 1.5)
                    return True
                except Exception:
                    fallback = await self.page.evaluate(
                        f"() => !!document.querySelector('{wait_for.split(',')[0].strip()}')"
                    )
                    if fallback:
                        return True
                    if attempt < retries:
                        await asyncio.sleep(attempt * 5.0)
            else:
                await jitter_sleep(0.8, 1.5)
                return True
        return False
