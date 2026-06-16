from __future__ import annotations

import asyncio
import logging
from playwright.async_api import async_playwright, Browser, Playwright

logger = logging.getLogger(__name__)

class PlaywrightRendererService:
    _instance: PlaywrightRendererService | None = None

    def __init__(self, max_concurrent: int = 2, max_renders_before_recycle: int = 50):
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._render_count = 0
        self._max_renders_before_recycle = max_renders_before_recycle

    @classmethod
    def get_instance(cls) -> PlaywrightRendererService:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def start(self) -> None:
        """Starts Playwright and the browser instance. Called in startup hooks."""
        async with self._lock:
            await self._ensure_browser_started()

    async def _ensure_browser_started(self) -> None:
        # Assumes self._lock is acquired
        if self._playwright is None:
            logger.info("Initializing Playwright instance...")
            self._playwright = await async_playwright().start()
        
        if self._browser is None or not self._browser.is_connected():
            logger.info("Launching headless Chromium browser...")
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-accelerated-2d-canvas",
                    "--disable-gpu",
                ]
            )

    async def stop(self) -> None:
        """Closes the browser and stops Playwright. Called in shutdown hooks."""
        async with self._lock:
            if self._browser is not None:
                logger.info("Closing Chromium browser...")
                try:
                    await self._browser.close()
                except Exception as e:
                    logger.error("Error closing browser: %s", e)
                self._browser = None
            if self._playwright is not None:
                logger.info("Stopping Playwright instance...")
                try:
                    await self._playwright.stop()
                except Exception as e:
                    logger.error("Error stopping Playwright: %s", e)
                self._playwright = None

    async def _recycle_browser(self) -> None:
        # Assumes self._lock is acquired
        logger.info("Recycling browser to prevent memory leak (render count reached %d)...", self._render_count)
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as e:
                logger.error("Error closing browser during recycle: %s", e)
            self._browser = None
        await self._ensure_browser_started()
        self._render_count = 0

    async def render_html(self, html_content: str, *, width: int, height: int, timeout: float = 5.0) -> bytes:
        """
        Renders HTML content in a headless Chromium page and returns screenshot bytes.
        Limits concurrency via semaphore, ensures connection, and auto-recycles on timeout.
        """
        async with self._semaphore:
            # Connection check & recycle check
            async with self._lock:
                if self._render_count >= self._max_renders_before_recycle:
                    await self._recycle_browser()
                else:
                    await self._ensure_browser_started()

            if self._browser is None:
                raise RuntimeError("Headless browser is not initialized.")

            try:
                return await asyncio.wait_for(
                    self._do_render(html_content, width, height),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                logger.error("HTML rendering timed out after %s seconds.", timeout)
                # Force-recycle browser under lock on timeout to ensure no hanging pages
                async with self._lock:
                    await self._recycle_browser()
                raise RuntimeError("Rendering timed out.")
            except Exception as e:
                logger.exception("Rendering failed due to error: %s", e)
                raise
            finally:
                async with self._lock:
                    self._render_count += 1

    async def _do_render(self, html_content: str, width: int, height: int) -> bytes:
        context = await self._browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=2
        )
        try:
            page = await context.new_page()
            await page.set_content(html_content, wait_until="networkidle")
            screenshot_bytes = await page.screenshot(type="png", full_page=True)
            return screenshot_bytes
        finally:
            await context.close()
