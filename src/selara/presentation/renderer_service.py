from __future__ import annotations

import asyncio
import logging
import time

from playwright.async_api import Browser, Playwright, async_playwright

from selara.core.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_RENDER_TIMEOUT_SECONDS = 15.0
RECYCLE_TIMEOUT_SECONDS = 20.0


class RenderTimeoutError(RuntimeError):
    """A render did not finish within its time budget. The browser is recycled before this is raised."""


class _RenderTrace:
    """Wall-clock time spent in each render phase, for logs on success and on timeout."""

    def __init__(self) -> None:
        self._phase = "queue"
        self._phase_started = time.perf_counter()
        self._durations_ms: dict[str, int] = {}

    @property
    def phase(self) -> str:
        return self._phase

    def enter(self, phase: str) -> None:
        now = time.perf_counter()
        self._durations_ms[self._phase] = round((now - self._phase_started) * 1000)
        self._phase = phase
        self._phase_started = now

    def summary(self) -> str:
        return " ".join(f"{name}={ms}ms" for name, ms in self._durations_ms.items())


class PlaywrightRendererService:
    _instance: PlaywrightRendererService | None = None

    def __init__(
        self,
        max_concurrent: int = 2,
        max_renders_before_recycle: int = 50,
        render_timeout_seconds: float = DEFAULT_RENDER_TIMEOUT_SECONDS,
    ):
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._render_count = 0
        self._max_renders_before_recycle = max_renders_before_recycle
        self._render_timeout_seconds = render_timeout_seconds

    @classmethod
    def get_instance(cls) -> PlaywrightRendererService:
        if cls._instance is None:
            cls._instance = cls(render_timeout_seconds=get_settings().render_timeout_seconds)
        return cls._instance

    async def start(self) -> None:
        """Starts Playwright eagerly when an explicit warm-up is needed."""
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

    async def render_html(
        self,
        html_content: str,
        *,
        width: int,
        height: int,
        timeout: float | None = None,
    ) -> bytes:
        """
        Renders HTML content in a headless Chromium page and returns screenshot bytes.
        Limits concurrency via semaphore, ensures connection, and auto-recycles on timeout.
        The timeout bounds page work (context, content, screenshot); it defaults to the configured render timeout.
        Raises RenderTimeoutError when that budget is exceeded.
        """
        budget = self._render_timeout_seconds if timeout is None else timeout
        trace = _RenderTrace()
        async with self._semaphore:
            trace.enter("browser")
            # Connection check & recycle check
            async with self._lock:
                if self._render_count >= self._max_renders_before_recycle:
                    await self._recycle_browser()
                else:
                    await self._ensure_browser_started()

            if self._browser is None:
                raise RuntimeError("Headless browser is not initialized.")

            try:
                screenshot_bytes = await asyncio.wait_for(
                    self._do_render(html_content, width, height, trace),
                    timeout=budget
                )
            except asyncio.TimeoutError:
                logger.error(
                    "HTML rendering timed out after %ss while in phase %s; completed phases: %s",
                    budget, trace.phase, trace.summary(),
                )
                # Force-recycle browser under lock on timeout to ensure no hanging pages.
                # A failed or hung recycle must not replace the timeout error; the next render relaunches the browser.
                async with self._lock:
                    try:
                        async with asyncio.timeout(RECYCLE_TIMEOUT_SECONDS):
                            await self._recycle_browser()
                    except Exception as e:
                        logger.error("Browser recycle after render timeout failed: %s", e)
                        self._browser = None
                raise RenderTimeoutError(f"Rendering timed out after {budget:g} seconds.") from None
            except Exception as e:
                logger.exception("Rendering failed due to error: %s", e)
                raise
            finally:
                async with self._lock:
                    self._render_count += 1
            trace.enter("done")
            logger.info("HTML render phases: %s", trace.summary())
            return screenshot_bytes

    async def _do_render(self, html_content: str, width: int, height: int, trace: _RenderTrace) -> bytes:
        trace.enter("context")
        context = await self._browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=2
        )
        try:
            trace.enter("page")
            page = await context.new_page()
            trace.enter("content")
            await page.set_content(html_content, wait_until="networkidle")
            trace.enter("screenshot")
            screenshot_bytes = await page.screenshot(type="png", full_page=True)
            return screenshot_bytes
        finally:
            await context.close()
