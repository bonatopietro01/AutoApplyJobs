"""Persistent, user-controlled LinkedIn browser authentication.

This module deliberately does not accept or enter LinkedIn credentials. The
user completes login, 2FA, and any security checks in the visible browser.
"""

from __future__ import annotations

import logging
import json
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator
from urllib.parse import urlparse
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import Browser, BrowserContext, Error as PlaywrightError, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE_DIR = PROJECT_ROOT / "browser_profile"
LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"
AUTHENTICATED_PATHS = ("/feed", "/jobs-tracker", "/my-items/saved-jobs")


def _is_authenticated_page(page: Page) -> bool:
    """Return whether the dedicated profile is on a signed-in LinkedIn page."""
    parsed = urlparse(page.url)
    host = parsed.hostname or ""
    path = parsed.path.rstrip("/").lower()
    return host.endswith("linkedin.com") and any(
        path == route or path.startswith(f"{route}/") for route in AUTHENTICATED_PATHS
    )


def _requires_manual_attention(page: Page) -> bool:
    path = urlparse(page.url).path.lower()
    return any(
        marker in path
        for marker in ("/login", "/signup", "/authwall", "/checkpoint")
    )


def _open_feed(page: Page) -> None:
    try:
        page.goto(LINKEDIN_FEED_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1_500)
    except PlaywrightTimeoutError:
        LOGGER.warning(
            "LinkedIn did not finish loading within 60 seconds. "
            "The browser remains available for manual inspection."
        )
    except PlaywrightError as navigation_error:
        LOGGER.warning(
            "LinkedIn navigation was interrupted (%s). The browser remains "
            "available; navigate to %s manually if needed.",
            navigation_error.message.splitlines()[0],
            LINKEDIN_FEED_URL,
        )


def _installed_browser_path() -> Path:
    """Find an installed Chrome or Edge executable without using its personal profile."""
    candidates = (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("Install Google Chrome or Microsoft Edge before using this app.")


def _launch_persistent_browser(
    playwright: object, profile_dir: Path
) -> tuple[Browser, BrowserContext]:
    """Start normal installed Chrome and attach Playwright through localhost CDP."""
    browser_path = _installed_browser_path()
    port_file = profile_dir / ".cdp-port"
    stored_port = port_file.read_text(encoding="ascii").strip() if port_file.is_file() else ""

    if stored_port:
        try:
            debug_port = int(stored_port)
            if not 1024 <= debug_port <= 65535:
                raise ValueError("port outside the valid range")
        except ValueError:
            debug_port = 0

        endpoint_is_active = False
        if debug_port:
            try:
                with urlopen(
                    f"http://127.0.0.1:{debug_port}/json/version", timeout=1
                ) as response:
                    endpoint_is_active = bool(
                        json.load(response).get("webSocketDebuggerUrl")
                    )
            except (URLError, OSError, ValueError):
                endpoint_is_active = False

        if endpoint_is_active:
            try:
                browser = playwright.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{debug_port}", timeout=10_000
                )
            except PlaywrightError as error:
                raise RuntimeError(
                    "Chrome is still open with the project profile, but Playwright "
                    "could not reconnect. Close that Chrome window and run the app again."
                ) from error
            if not browser.contexts:
                browser.close()
                raise RuntimeError("The existing Chrome session has no browser context.")
            LOGGER.info("Reconnected to the existing project Chrome session.")
            return browser, browser.contexts[0]
        LOGGER.info("The saved Chrome connection is no longer active; reopening it.")

    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        debug_port = port_socket.getsockname()[1]
    port_file.write_text(str(debug_port), encoding="ascii")

    subprocess.Popen(
        [
            str(browser_path),
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={debug_port}",
            f"--remote-allow-origins=http://127.0.0.1:{debug_port}",
            LINKEDIN_FEED_URL,
        ],
        close_fds=True,
    )

    endpoint = f"http://127.0.0.1:{debug_port}"
    deadline = time.monotonic() + 30
    browser: Browser | None = None
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            browser = playwright.chromium.connect_over_cdp(endpoint, timeout=2_000)
            break
        except PlaywrightError as error:
            last_error = error
            time.sleep(0.25)

    if browser is None:
        raise RuntimeError(
            "Chrome opened, but Playwright could not attach to its local session."
        ) from last_error
    if not browser.contexts:
        browser.close()
        raise RuntimeError("Chrome opened without a usable browser context.")

    LOGGER.info(
        "Using %s with a dedicated local profile for manual authentication.",
        browser_path.name,
    )
    return browser, browser.contexts[0]


@contextmanager
def persistent_browser_context(
    profile_dir: Path = DEFAULT_PROFILE_DIR,
) -> Iterator[BrowserContext]:
    """Connect to the project browser and disconnect without closing Chrome."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser, context = _launch_persistent_browser(playwright, profile_dir)
        try:
            yield context
        finally:
            browser.close()


def authenticate(profile_dir: Path = DEFAULT_PROFILE_DIR) -> bool:
    """Open LinkedIn with a persistent profile and verify manual authentication."""
    LOGGER.info("Using the local persistent browser profile at %s", profile_dir)

    with persistent_browser_context(profile_dir) as context:
        page = context.pages[0] if context.pages else context.new_page()

        if not _is_authenticated_page(page) or _requires_manual_attention(page):
            _open_feed(page)
        if _is_authenticated_page(page) and not _requires_manual_attention(page):
            LOGGER.info("LinkedIn session verified. No login was required.")
            return True

        print(
            "\nManual action required in the Chromium window:\n"
            "1. Sign in to LinkedIn yourself.\n"
            "2. Complete any 2FA, CAPTCHA, or security check yourself.\n"
            "3. When your LinkedIn feed is visible, return here and press Enter.\n"
            "\nYour username and password are never requested or stored by this app."
        )
        input("\nPress Enter only after the LinkedIn feed is visible... ")

        if _is_authenticated_page(page) and not _requires_manual_attention(page):
            LOGGER.info("LinkedIn session verified and saved in the local profile.")
            return True

        LOGGER.error(
            "Authentication could not be verified. Complete the visible LinkedIn "
            "prompt, then run this command again."
        )
        return False
