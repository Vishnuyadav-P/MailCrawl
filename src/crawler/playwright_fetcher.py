"""
Playwright headless Chromium fetcher for JavaScript-rendered web pages.
"""

import asyncio
from typing import Optional

from src.utils.config import Config
from src.utils.logging import logger
from src.validation.domain_validator import validate_url_ssrf


class PlaywrightFetcher:
    """Async wrapper around Playwright Chromium browser."""
    def __init__(self):
        self._playwright = None
        self._browser = None

    async def start(self):
        """Launches the Chromium browser instance."""
        try:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            logger.info("Playwright headless Chromium browser initialized.")
        except Exception as exc:
            logger.warning(f"Could not initialize Playwright: {exc}. JS rendering fallback will be disabled.")
            self._playwright = None
            self._browser = None

    async def fetch_html(self, url: str, timeout_seconds: int = 15) -> Optional[str]:
        """Navigates to URL, waits for network idle / DOM content loaded, and returns full rendered HTML."""
        if not self._browser:
            return None

        is_safe, reason = await asyncio.to_thread(validate_url_ssrf, url)
        if not is_safe:
            logger.warning(f"Playwright SSRF check blocked '{url}': {reason}")
            return None

        context = None
        page = None
        try:
            context = await self._browser.new_context(
                user_agent=Config.DEFAULT_USER_AGENT,
                viewport={"width": 1280, "height": 800}
            )
            page = await context.new_page()
            # Navigate with timeout
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=timeout_seconds * 1000
            )

            if response and response.status >= 400:
                logger.warning(f"Playwright received HTTP status {response.status} for '{url}'")

            # Allow minor pause for dynamic JS scripts to execute
            await page.wait_for_timeout(1000)

            content = await page.content()
            return content

        except Exception as exc:
            logger.warning(f"Playwright failed to render '{url}': {exc}")
            return None
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
            if context:
                try:
                    await context.close()
                except Exception:
                    pass

    async def stop(self):
        """Closes browser and stops Playwright."""
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._browser = None
        self._playwright = None
