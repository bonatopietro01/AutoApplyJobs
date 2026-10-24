"""Conservative, one-job-at-a-time reader for manually saved LinkedIn jobs."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import BrowserContext, Error as PlaywrightError, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from linkedin_connector.browser import (
    DEFAULT_PROFILE_DIR,
    _is_authenticated_page,
    _requires_manual_attention,
    persistent_browser_context,
)
from linkedin_connector.models import LinkedInJob


LOGGER = logging.getLogger(__name__)
SAVED_JOBS_URL = "https://www.linkedin.com/my-items/saved-jobs/"
# LinkedIn may render a hidden duplicate of the card list for responsive layout.
# Select the visible anchor so the reader does not mistake a hidden clone for a job.
JOB_LINK_SELECTOR = 'a[href*="/jobs/view/"]:visible'
NEXT_PAGE_PATTERN = re.compile(r"^(?:Avanti|Next)$", re.IGNORECASE)
DESCRIPTION_HEADINGS = (
    "Informazioni sull’offerta di lavoro",
    "Informazioni sull'offerta di lavoro",
    "Dettagli dell’offerta di lavoro",
    "Dettagli dell'offerta di lavoro",
    "Descrizione dell’offerta di lavoro",
    "Descrizione dell'offerta di lavoro",
    "About the job",
    "Job description",
)
DESCRIPTION_CONTENT_SELECTORS = (
    "[data-job-description]",
    "[class*='jobs-description-content__text']",
    "[class*='jobs-description__content']",
    "[class*='jobs-box__html-content']",
)
DESCRIPTION_END_HEADINGS = (
    "Imposta un avviso per offerte di lavoro simili",
    "Set alert for similar jobs",
    "Informazioni sull’azienda",
    "About the company",
)
DESCRIPTION_HEADING_PATTERN = re.compile(
    "|".join(re.escape(marker) for marker in DESCRIPTION_HEADINGS), re.IGNORECASE
)
EXPAND_DESCRIPTION_PATTERN = re.compile(
    r"^(?:(?:…|\.\.\.)\s*)?(?:altro|more|mostra altro|show more)\s*$",
    re.IGNORECASE,
)
JOB_ID_PATTERN = re.compile(r"/jobs/view/(\d+)", re.IGNORECASE)
SAVED_COUNT_PATTERN = re.compile(
    r"\b(?:Salvate|Saved)\s*[^\d\r\n]{0,16}(\d{1,4})\b", re.IGNORECASE
)
EMPTY_SAVED_STATE_PATTERN = re.compile(
    r"\b(?:no saved jobs(?: yet)?|you (?:haven't|have not) saved any jobs|"
    r"you have no saved jobs|non hai (?:ancora )?salvato (?:nessun[ao]?|alcun[ao]?) "
    r"(?:annunci[oi]|offert[ae]|posizion[ei]|lavor[oi])|"
    r"nessun[ao]? (?:annunci[oi]|offert[ae]|posizion[ei]|lavor[oi]) salvato)\b",
    re.IGNORECASE,
)
CHALLENGE_TEXT_PATTERN = re.compile(
    r"captcha|security verification|unusual activity|rate limit|too many requests|"
    r"verifica la tua identità|attività insolita",
    re.IGNORECASE,
)
MAX_SAVED_JOBS_PER_RUN = 100
MAX_PAGINATION_PAGES = 20
DETAIL_REQUEST_DELAY_SECONDS = 1.5
DETAIL_RETRY_DELAY_SECONDS = 0.75
DESCRIPTION_RENDER_POLL_SECONDS = 3.0
DESCRIPTION_RENDER_POLL_INTERVAL_MS = 250
DESCRIPTION_RENDER_TEXT_TIMEOUT_MS = 750


class LinkedInReadError(RuntimeError):
    """The saved job could not be read reliably without guessing."""


class ManualLinkedInActionRequired(LinkedInReadError):
    """LinkedIn requires the user to complete a login or security prompt."""


class SavedJobCards(list[LinkedInJob]):
    """Readable details plus the separately verified IDs from the Saved list."""

    def __init__(
        self,
        jobs: list[LinkedInJob],
        *,
        count_mismatch: bool = False,
        visible_ids: list[str] | None = None,
        membership_verified: bool | None = None,
        unreadable: list[dict[str, str | None]] | None = None,
        skipped_missing_title: list[dict[str, str | None]] | None = None,
        outcomes: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__(jobs)
        self.count_mismatch = count_mismatch
        self.visible_ids = list(dict.fromkeys(visible_ids or (job.linkedin_job_id for job in jobs)))
        self.membership_verified = (
            not count_mismatch if membership_verified is None else membership_verified
        )
        self.unreadable = list(unreadable or [])
        self.skipped_missing_title = list(skipped_missing_title or [])
        self.outcomes = list(outcomes or [])


def _linkedin_page(context: BrowserContext) -> Page:
    pages = [
        page
        for page in context.pages
        if (urlparse(page.url).hostname or "").endswith("linkedin.com")
    ]
    pages.sort(
        key=lambda page: (
            0
            if urlparse(page.url).path.rstrip("/").lower()
            in {"/jobs-tracker", "/my-items/saved-jobs"}
            else 1
            if urlparse(page.url).path.rstrip("/").lower() == "/feed"
            else 2
        )
    )
    return pages[0] if pages else context.new_page()


def _parse_job_card(href: str, paragraphs: list[str]) -> LinkedInJob:
    """Parse the semantic paragraph text from one job link on the saved list."""
    absolute_url = urljoin(SAVED_JOBS_URL, href)
    job_match = JOB_ID_PATTERN.search(urlparse(absolute_url).path)
    if not job_match:
        raise LinkedInReadError("The first saved-job link did not contain a LinkedIn job ID.")
    title = paragraphs[0].strip() if paragraphs else ""
    company: str | None = None
    location: str | None = None
    if title and len(paragraphs) > 1:
        company_location = re.sub(r"\s+", " ", paragraphs[1]).strip()
        if "·" in company_location:
            company, location = (
                part.strip() or None for part in company_location.split("·", 1)
            )
        elif company_location:
            company = company_location

    canonical_url = f"https://www.linkedin.com/jobs/view/{job_match.group(1)}/"
    return LinkedInJob(
        linkedin_job_id=job_match.group(1),
        title=title,
        company=company,
        location=location,
        linkedin_url=canonical_url,
    )


def _ensure_signed_in(page: Page, *, allow_job_detail: bool = False) -> None:
    parsed = urlparse(page.url)
    is_linkedin_job_detail = (
        (parsed.hostname or "").endswith("linkedin.com")
        and JOB_ID_PATTERN.search(parsed.path) is not None
    )
    if _requires_manual_attention(page) or not (
        _is_authenticated_page(page) or (allow_job_detail and is_linkedin_job_detail)
    ):
        raise ManualLinkedInActionRequired(
            "LinkedIn needs manual attention in the project Chrome window. "
            "Complete the login or security prompt yourself, then run the reader again."
        )


def _check_page_access(page: Page, response_status: int | None = None) -> None:
    if response_status in {403, 429, 999}:
        raise ManualLinkedInActionRequired(
            f"LinkedIn returned HTTP {response_status}; stopping without retrying. "
            "Inspect the project Chrome window and complete any required action yourself."
        )
    if response_status is not None and response_status >= 400:
        raise LinkedInReadError(
            f"LinkedIn returned HTTP {response_status}; the reader stopped without retrying."
        )
    if _requires_manual_attention(page):
        raise ManualLinkedInActionRequired(
            "LinkedIn redirected to a login or security-check page. "
            "Complete it manually in the project Chrome window, then rerun the reader."
        )
    try:
        initial_text = page.locator("body").inner_text(timeout=2_000)[:1_800]
    except PlaywrightError:
        initial_text = ""
    if CHALLENGE_TEXT_PATTERN.search(initial_text):
        raise ManualLinkedInActionRequired(
            "LinkedIn displayed a security or rate-limit message. "
            "The reader stopped; inspect Chrome and do not retry until LinkedIn is clear."
        )


def _parse_expected_saved_count(header_text: str) -> int | None:
    match = SAVED_COUNT_PATTERN.search(header_text[:2_500])
    return int(match.group(1)) if match else None


def _expected_saved_count(page: Page) -> int | None:
    try:
        header_text = page.locator("body").inner_text(timeout=3_000)
    except PlaywrightError:
        return None
    return _parse_expected_saved_count(header_text)


def _open_saved_jobs(page: Page, *, enforce_safety_limit: bool = True) -> int | None:
    _ensure_signed_in(page)
    try:
        response = page.goto(SAVED_JOBS_URL, wait_until="domcontentloaded", timeout=45_000)
    except PlaywrightTimeoutError:
        LOGGER.warning("Saved Jobs navigation timed out; checking the visible page once.")
        response = None
    except PlaywrightError as error:
        raise LinkedInReadError(
            f"Could not open LinkedIn Saved Jobs ({error.message.splitlines()[0]})."
        ) from error

    _ensure_signed_in(page)
    _check_page_access(page, response.status if response else None)
    try:
        page.locator("body").wait_for(state="visible", timeout=10_000)
    except PlaywrightTimeoutError as error:
        raise LinkedInReadError("LinkedIn Saved Jobs did not render visible page content.") from error

    saved_count = None
    for _ in range(20):
        saved_count = _expected_saved_count(page)
        if saved_count is not None:
            break
        page.wait_for_timeout(250)
    if saved_count is not None:
        if enforce_safety_limit and saved_count > MAX_SAVED_JOBS_PER_RUN:
            raise LinkedInReadError(
                f"The Saved Jobs counter reports {saved_count}; the safety limit is "
                f"{MAX_SAVED_JOBS_PER_RUN} jobs per run. No job details were opened."
            )
        LOGGER.info("LinkedIn reports %d jobs in the Saved tab.", saved_count)
    else:
        LOGGER.warning("Could not read the Saved tab counter; using bounded pagination.")
    return saved_count


def _description_from_text(main_text: str) -> tuple[str | None, bool]:
    start_marker = next(
        (marker for marker in DESCRIPTION_HEADINGS if marker.casefold() in main_text.casefold()),
        None,
    )
    if start_marker is None:
        return None, False

    start = main_text.casefold().find(start_marker.casefold()) + len(start_marker)
    description_area = main_text[start:]
    end_positions = [
        position
        for marker in DESCRIPTION_END_HEADINGS
        if (position := description_area.casefold().find(marker.casefold())) >= 0
    ]
    if end_positions:
        description_area = description_area[: min(end_positions)]

    lines = [line.strip() for line in description_area.splitlines()]
    truncated = any(EXPAND_DESCRIPTION_PATTERN.fullmatch(line) for line in lines)
    while lines and not lines[0]:
        lines.pop(0)
    while lines and (
        not lines[-1]
        or EXPAND_DESCRIPTION_PATTERN.fullmatch(lines[-1])
        or lines[-1].casefold() in {"mostra meno", "see less"}
    ):
        lines.pop()

    description = "\n".join(lines).strip()
    return (description, not truncated) if description else (None, False)


def _expand_description(page: Page, main: Page) -> bool:
    headings = main.locator("h1, h2, h3").filter(has_text=DESCRIPTION_HEADING_PATTERN)
    if not headings.count():
        return False

    ancestors = []
    ancestor = headings.first
    for _ in range(8):
        ancestor = ancestor.locator("xpath=..")
        ancestors.append(ancestor)

    # LinkedIn changes the button's nesting and sometimes places an overlay above it.
    # Try the nearest description containers first, scroll into view, then use a
    # scoped forced click as a fallback. The caller still refuses incomplete text.
    attempted = 0
    found_control = False
    for scope in [*ancestors, main]:
        controls = scope.locator("button").filter(has_text=EXPAND_DESCRIPTION_PATTERN)
        found_control = found_control or controls.count() > 0
        for index in range(min(controls.count(), 3)):
            control = controls.nth(index)
            try:
                if not control.is_visible():
                    continue
                try:
                    control.scroll_into_view_if_needed(timeout=2_000)
                except PlaywrightError:
                    pass
                for force in (False, True):
                    try:
                        control.click(timeout=2_000, force=force)
                        page.wait_for_timeout(500)
                        main_text = main.first.inner_text(timeout=5_000)
                        _description, complete = _description_from_text(main_text)
                        if complete:
                            return True
                        attempted += 1
                        if attempted >= 4:
                            break
                    except PlaywrightError:
                        attempted += 1
                if attempted >= 4:
                    break
            except PlaywrightError:
                attempted += 1
            if attempted >= 4:
                break
        if attempted >= 4:
            break
    if found_control:
        LOGGER.warning("LinkedIn's description expansion control could not be activated or verified.")
    return False


def _description_text(page: Page) -> tuple[str | None, bool]:
    main = page.locator("main")
    try:
        main.first.wait_for(state="visible", timeout=15_000)
    except PlaywrightTimeoutError:
        LOGGER.warning("LinkedIn job detail did not expose visible main content.")
        return None, False

    deadline = time.monotonic() + DESCRIPTION_RENDER_POLL_SECONDS
    best_partial: str | None = None
    expansion_attempted = False
    while True:
        remaining_ms = max(100, int((deadline - time.monotonic()) * 1000))
        headings = main.locator("h1, h2, h3").filter(has_text=DESCRIPTION_HEADING_PATTERN)
        has_heading = False
        try:
            has_heading = headings.count() > 0
            if has_heading:
                headings.first.wait_for(state="visible", timeout=min(500, remaining_ms))
        except (PlaywrightTimeoutError, PlaywrightError):
            has_heading = False
        if has_heading and not expansion_attempted:
            _expand_description(page, main)
            expansion_attempted = True
        try:
            main_text = main.first.inner_text(
                timeout=min(DESCRIPTION_RENDER_TEXT_TIMEOUT_MS, remaining_ms)
            )
        except PlaywrightError:
            main_text = ""
        description, complete = _description_from_text(main_text)
        if description and complete:
            return description, True
        if description:
            best_partial = description

        # LinkedIn sometimes renders the description container later or uses
        # a different localized heading. Read only known description nodes;
        # never interpret the whole detail page as the job description.
        for selector in DESCRIPTION_CONTENT_SELECTORS:
            if time.monotonic() >= deadline:
                break
            try:
                candidates = main.locator(selector)
                for index in range(min(candidates.count(), 3)):
                    if time.monotonic() >= deadline:
                        break
                    candidate = candidates.nth(index)
                    if not candidate.is_visible():
                        continue
                    text = candidate.inner_text(
                        timeout=min(400, max(100, int((deadline - time.monotonic()) * 1000)))
                    ).strip()
                    if not text:
                        continue
                    parsed, candidate_complete = _description_from_text(
                        f"{DESCRIPTION_HEADINGS[0]}\n{text}"
                    )
                    if parsed and candidate_complete:
                        return parsed, True
                    if parsed:
                        best_partial = parsed
            except (PlaywrightError, PlaywrightTimeoutError):
                continue

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        wait_ms = min(DESCRIPTION_RENDER_POLL_INTERVAL_MS, max(1, int(remaining * 1000)))
        try:
            page.wait_for_timeout(wait_ms)
        except (AttributeError, PlaywrightError):
            time.sleep(wait_ms / 1000)

    if best_partial:
        return best_partial, False
    LOGGER.warning(
        "LinkedIn job detail exposed neither a supported description heading nor a readable description container."
    )
    return None, False


def _visible_job_ids(page: Page) -> list[str]:
    ids: list[str] = []
    links = page.locator(JOB_LINK_SELECTOR)
    for index in range(links.count()):
        href = links.nth(index).get_attribute("href") or ""
        match = JOB_ID_PATTERN.search(urlparse(urljoin(SAVED_JOBS_URL, href)).path)
        if match and match.group(1) not in ids:
            ids.append(match.group(1))
    return ids


def _collect_current_page(page: Page, found: dict[str, LinkedInJob], limit: int | None) -> None:
    links = page.locator(JOB_LINK_SELECTOR)
    for index in range(links.count()):
        link = links.nth(index)
        href = link.get_attribute("href") or ""
        match = JOB_ID_PATTERN.search(urlparse(urljoin(SAVED_JOBS_URL, href)).path)
        if not match or match.group(1) in found:
            continue
        paragraphs = [
            text.strip()
            for text in link.locator("p").all_text_contents()
            if text.strip()
        ]
        try:
            found[match.group(1)] = _parse_job_card(href, paragraphs)
        except LinkedInReadError as error:
            # Keep the ID in the independently collected visible-ID snapshot.
            # A card without readable text must not truncate pagination.
            LOGGER.warning("Saved job card %s is visible but unreadable: %s", match.group(1), error)
            continue
        if len(found) > MAX_SAVED_JOBS_PER_RUN:
            raise LinkedInReadError(
                f"More than {MAX_SAVED_JOBS_PER_RUN} saved jobs were found; "
                "stopping at the per-run safety limit."
            )
        if limit is not None and len(found) >= limit:
            return


def _has_explicit_empty_saved_state(page: Page) -> bool:
    """Accept an empty list only when LinkedIn explicitly renders its empty state."""
    try:
        if _visible_job_ids(page):
            return False
        next_buttons = page.get_by_role("button", name=NEXT_PAGE_PATTERN)
        if next_buttons.count() and next_buttons.first.is_enabled():
            return False
        body_text = page.locator("body").inner_text(timeout=2_000)
    except PlaywrightError:
        return False
    return EMPTY_SAVED_STATE_PATTERN.search(body_text) is not None


def _advance_saved_jobs_page(page: Page, previous_ids: list[str]) -> bool:
    next_buttons = page.get_by_role("button", name=NEXT_PAGE_PATTERN)
    if not next_buttons.count() or not next_buttons.first.is_enabled():
        return False
    if next_buttons.count() > 1:
        raise LinkedInReadError("LinkedIn exposed ambiguous Saved Jobs pagination controls.")

    # A click can finish while Playwright is still waiting for navigation.
    # Never repeat it: observe the rendered IDs instead, including after timeout.
    try:
        next_buttons.first.click(timeout=5_000)
    except PlaywrightTimeoutError:
        LOGGER.info("Next click timed out; verifying rendered Saved Jobs before any further action.")
    except PlaywrightError as error:
        raise LinkedInReadError("Saved Jobs pagination could not be read; local jobs were kept.") from error
    previous = frozenset(previous_ids)
    candidate = None
    stable_samples = 0
    for _ in range(24):
        try:
            page.wait_for_timeout(250)
            _check_page_access(page)
            current = frozenset(_visible_job_ids(page))
        except PlaywrightError:
            continue
        if current and current != previous:
            stable_samples = stable_samples + 1 if current == candidate else 1
            candidate = current
            if stable_samples >= 3:
                return True
        else:
            candidate = None
            stable_samples = 0
    raise LinkedInReadError(
        "Saved Jobs pagination could not be verified after Next; local jobs were kept."
    )


def _collect_saved_job_cards_pass(
    page: Page,
    saved_count: int | None,
    limit: int | None = None,
    *,
    visible_ids_out: list[str] | None = None,
) -> tuple[list[LinkedInJob], bool]:
    target = min(saved_count, limit) if saved_count is not None and limit is not None else (
        saved_count if limit is None else limit
    )
    found: dict[str, LinkedInJob] = {}
    reached_last_page = False
    seen_pages: set[frozenset[str]] = set()
    seen_visible_ids: set[str] = set()

    for page_number in range(1, MAX_PAGINATION_PAGES + 1):
        if saved_count == 0 and _has_explicit_empty_saved_state(page):
            reached_last_page = True
            break
        try:
            page.locator(JOB_LINK_SELECTOR).first.wait_for(state="visible", timeout=15_000)
        except PlaywrightTimeoutError as error:
            raise LinkedInReadError(
                "No visible saved-job cards were found on the current page."
            ) from error

        ids_before = _visible_job_ids(page)
        signature = frozenset(ids_before)
        if not signature or signature in seen_pages:
            raise LinkedInReadError("Saved Jobs pagination repeated or exposed an unreadable page; local jobs were kept.")
        seen_pages.add(signature)
        seen_visible_ids.update(ids_before)
        if len(seen_visible_ids) > MAX_SAVED_JOBS_PER_RUN:
            raise LinkedInReadError(
                f"Saved Jobs exceeded the {MAX_SAVED_JOBS_PER_RUN}-job safety limit."
            )
        if visible_ids_out is not None:
            for job_id in ids_before:
                if job_id not in visible_ids_out:
                    visible_ids_out.append(job_id)
        _collect_current_page(page, found, limit)
        collected_ids = len(visible_ids_out) if visible_ids_out is not None else len(found)
        LOGGER.info(
            "Saved Jobs page %d: %d unique visible job IDs collected%s.",
            page_number,
            collected_ids,
            f" of {target}" if target is not None else "",
        )

        if limit is not None and len(found) >= limit:
            break
        if len(found) >= MAX_SAVED_JOBS_PER_RUN:
            if _advance_saved_jobs_page(page, ids_before):
                raise LinkedInReadError(
                    f"Saved Jobs exceeded the {MAX_SAVED_JOBS_PER_RUN}-job safety limit."
                )
            reached_last_page = True
            break
        if not _advance_saved_jobs_page(page, ids_before):
            reached_last_page = True
            break
    else:
        raise LinkedInReadError(
            f"Saved Jobs exceeded the {MAX_PAGINATION_PAGES}-page safety limit."
        )

    return list(found.values()), reached_last_page


def _collect_saved_job_cards(
    page: Page, saved_count: int | None, limit: int | None = None
) -> SavedJobCards:
    """Verify a count mismatch against a fresh, complete reading of the Saved list."""
    if saved_count == 0:
        first_ids: list[str] = []
        first_cards, first_complete = _collect_saved_job_cards_pass(
            page, saved_count, limit, visible_ids_out=first_ids
        )
        if first_cards or first_ids or not first_complete:
            raise LinkedInReadError(
                "LinkedIn reports no saved jobs, but its explicit empty list could not be verified."
            )
        refreshed_count = _open_saved_jobs(page)
        if refreshed_count != 0:
            raise LinkedInReadError(
                "LinkedIn's Saved list changed during empty-list verification; local jobs were kept."
            )
        verified_ids: list[str] = []
        verified_cards, verified_complete = _collect_saved_job_cards_pass(
            page, refreshed_count, limit, visible_ids_out=verified_ids
        )
        if verified_cards or verified_ids or not verified_complete:
            raise LinkedInReadError(
                "LinkedIn's empty Saved list was not confirmed by a second complete reading."
            )
        LOGGER.info("LinkedIn's explicitly empty Saved list was confirmed by two complete readings.")
        return SavedJobCards([], visible_ids=[], membership_verified=True)
    visible_ids: list[str] = []
    cards, reached_last_page = _collect_saved_job_cards_pass(
        page, saved_count, limit, visible_ids_out=visible_ids
    )
    if limit is not None:
        # Single-item previews intentionally stop before full pagination; only
        # use the selected card for their local count comparison.
        visible_ids = [job.linkedin_job_id for job in cards]
    elif not visible_ids:
        visible_ids = [job.linkedin_job_id for job in cards]
    target = min(saved_count, limit) if saved_count is not None and limit is not None else saved_count
    if limit is None and not reached_last_page:
        raise LinkedInReadError("Saved Jobs pagination was not verified; the local snapshot was kept.")
    if saved_count == 0 and limit is None and not cards:
        # The counter alone cannot prove that every local Saved job was removed.
        return SavedJobCards([], count_mismatch=True, visible_ids=[], membership_verified=False)
    if target is None or len(visible_ids) == target:
        return SavedJobCards(
            cards,
            visible_ids=visible_ids,
            membership_verified=limit is None and reached_last_page,
        )
    if not reached_last_page:
        raise LinkedInReadError("The Saved Jobs list ended before its pagination could be verified.")

    LOGGER.warning(
        "LinkedIn reports %d saved jobs but exposed %d unique visible IDs; reading the complete list once more.",
        saved_count, len(visible_ids),
    )
    refreshed_count = _open_saved_jobs(page)
    if refreshed_count is None:
        raise LinkedInReadError(
            "LinkedIn's Saved Jobs counter disappeared during verification; the local snapshot was kept."
        )
    verified_ids: list[str] = []
    verified_cards, verified_last_page = _collect_saved_job_cards_pass(
        page, refreshed_count, limit, visible_ids_out=verified_ids
    )
    if not verified_ids:
        verified_ids = [job.linkedin_job_id for job in verified_cards]
    verified_target = min(refreshed_count, limit) if limit is not None else refreshed_count
    if len(verified_ids) == verified_target and (limit is not None or verified_last_page):
        return SavedJobCards(
            verified_cards,
            visible_ids=verified_ids,
            membership_verified=limit is None and verified_last_page,
        )

    first_ids = set(visible_ids)
    verified_id_set = set(verified_ids)
    stable_membership = (
        limit is None and reached_last_page and verified_last_page and
        first_ids == verified_id_set and len(visible_ids) == len(verified_ids)
    )
    if stable_membership:
        LOGGER.warning(
            "LinkedIn's Saved Jobs counter reports %d then %d, but two complete readings found "
            "the same %d visible job IDs. Using the repeatedly verified IDs for reconciliation.",
            saved_count, refreshed_count, len(verified_ids),
        )
        return SavedJobCards(
            verified_cards,
            count_mismatch=True,
            visible_ids=verified_ids,
            membership_verified=True,
        )
    raise LinkedInReadError(
        f"LinkedIn reports {refreshed_count} saved jobs, but only {len(verified_ids)} unique "
        "visible job IDs were found after verification; the previous local snapshot was kept."
    )


def _ids_before_first_existing(
    visible_ids: list[str], existing_ids: set[str]
) -> tuple[list[str], bool]:
    """Find the new-job prefix and whether the first known job was reached."""
    new_ids: list[str] = []
    seen: set[str] = set()
    for job_id in visible_ids:
        if job_id in existing_ids:
            return new_ids, True
        if job_id not in seen:
            seen.add(job_id)
            new_ids.append(job_id)
    return new_ids, False


def _collect_new_saved_job_cards(
    page: Page, saved_count: int | None, existing_ids: set[str]
) -> list[LinkedInJob]:
    """Read recent job cards only, stopping when the local snapshot is reached.

    LinkedIn presents newly saved jobs ahead of older saved jobs. The first ID
    already in SQLite is therefore the incremental boundary; older cards and
    their detail pages need not be revisited on each dashboard refresh.
    """
    if saved_count == 0:
        return []
    found: dict[str, LinkedInJob] = {}

    for page_number in range(1, MAX_PAGINATION_PAGES + 1):
        try:
            page.locator(JOB_LINK_SELECTOR).first.wait_for(state="visible", timeout=15_000)
        except PlaywrightTimeoutError as error:
            raise LinkedInReadError(
                "No visible saved-job cards were found on the current page."
            ) from error

        visible_ids = _visible_job_ids(page)
        new_ids, reached_existing = _ids_before_first_existing(visible_ids, existing_ids)
        new_id_set = set(new_ids)
        links = page.locator(JOB_LINK_SELECTOR)
        for index in range(links.count()):
            link = links.nth(index)
            href = link.get_attribute("href") or ""
            match = JOB_ID_PATTERN.search(urlparse(urljoin(SAVED_JOBS_URL, href)).path)
            if not match:
                continue
            job_id = match.group(1)
            if job_id in existing_ids:
                break
            if job_id not in new_id_set or job_id in found:
                continue
            paragraphs = [
                text.strip()
                for text in link.locator("p").all_text_contents()
                if text.strip()
            ]
            found[job_id] = _parse_job_card(href, paragraphs)
            if len(found) > MAX_SAVED_JOBS_PER_RUN:
                raise LinkedInReadError(
                    f"More than {MAX_SAVED_JOBS_PER_RUN} new saved jobs were found; "
                    "stopping at the per-run safety limit."
                )

        LOGGER.info(
            "Incremental Saved Jobs page %d: %d new job cards collected%s.",
            page_number,
            len(found),
            "; an already-imported job was reached" if reached_existing else "",
        )
        if reached_existing:
            return list(found.values())
        if len(found) >= MAX_SAVED_JOBS_PER_RUN:
            return list(found.values())
        if not _advance_saved_jobs_page(page, visible_ids):
            return list(found.values())

    raise LinkedInReadError(
        f"Saved Jobs exceeded the {MAX_PAGINATION_PAGES}-page incremental safety limit."
    )


def _detail_title(page: Page) -> str | None:
    """Read a conservative job title from the visible detail-page main heading."""
    try:
        headings = page.locator("main").locator("h1")
        if headings.count() != 1 or not headings.first.is_visible():
            return None
        title = re.sub(r"\s+", " ", headings.first.inner_text(timeout=1_500)).strip()
    except PlaywrightError:
        return None
    if not title or len(title) > 200 or title.casefold() in {"jobs", "job details", "dettagli"}:
        return None
    return title


def _read_job_detail(
    context: BrowserContext,
    job: LinkedInJob,
    *,
    require_complete_description: bool,
    outcome: dict[str, object] | None = None,
) -> LinkedInJob:
    detail_page = context.new_page()
    if outcome is not None:
        outcome["page_opened"] = True
    try:
        try:
            response = detail_page.goto(
                job.linkedin_url,
                wait_until="domcontentloaded",
                timeout=45_000,
            )
        except PlaywrightTimeoutError:
            LOGGER.warning("Job detail navigation timed out; checking the visible page once.")
            response = None

        _check_page_access(detail_page, response.status if response else None)
        _ensure_signed_in(detail_page, allow_job_detail=True)
        actual_id = JOB_ID_PATTERN.search(urlparse(detail_page.url).path)
        if not actual_id or actual_id.group(1) != job.linkedin_job_id:
            raise LinkedInReadError(
                f"LinkedIn opened a different page for saved job {job.linkedin_job_id}."
            )
        if outcome is not None:
            outcome["detail_opened"] = True

        title = job.title.strip() or (_detail_title(detail_page) or "")
        if outcome is not None:
            outcome["title"] = title or None

        description, description_complete = _description_text(detail_page)
        if outcome is not None:
            outcome["description_read_status"] = (
                "complete" if description and description_complete
                else "partial" if description else "failed"
            )
        if not description:
            raise LinkedInReadError(
                f"LinkedIn did not expose a readable description for saved job "
                f"{job.linkedin_job_id}."
            )
        if require_complete_description and not description_complete:
            raise LinkedInReadError(
                f"Description for saved job {job.linkedin_job_id} remains collapsed; "
                "the reader will skip this detail and continue with the next saved job."
            )
        return LinkedInJob(
            linkedin_job_id=job.linkedin_job_id,
            title=title,
            company=job.company,
            location=job.location,
            linkedin_url=job.linkedin_url,
            description=description,
            description_complete=description_complete,
        )
    finally:
        detail_page.close()


def _read_saved_jobs(
    profile_dir: Path,
    *,
    limit: int | None,
    require_complete_descriptions: bool,
    existing_job_ids: set[str] | None = None,
) -> list[LinkedInJob]:
    with persistent_browser_context(profile_dir) as context:
        list_page = _linkedin_page(context)
        incremental = existing_job_ids is not None
        saved_count = _open_saved_jobs(
            list_page, enforce_safety_limit=not incremental
        )
        cards = (
            _collect_new_saved_job_cards(list_page, saved_count, existing_job_ids)
            if incremental and existing_job_ids is not None
            else _collect_saved_job_cards(list_page, saved_count, limit)
        )
        if not cards and not list(getattr(cards, "visible_ids", ()) or ()):
            LOGGER.info(
                "No new saved LinkedIn jobs were found."
                if incremental
                else "No saved LinkedIn jobs were found."
            )
            return cards

        results: list[LinkedInJob] = []
        unreadable: list[dict[str, str | None]] = []
        skipped_missing_title: list[dict[str, str | None]] = []
        outcomes: list[dict[str, object]] = []
        card_by_id = {card.linkedin_job_id: card for card in cards}
        visible_ids = list(getattr(cards, "visible_ids", ()) or card_by_id)
        for missing_id in visible_ids:
            if missing_id not in card_by_id:
                # The verified visible URL contains the ID, so still open its
                # detail page and try to recover a title from the page heading.
                card_by_id[missing_id] = LinkedInJob(
                    linkedin_job_id=missing_id,
                    title="",
                    company=None,
                    location=None,
                    linkedin_url=f"https://www.linkedin.com/jobs/view/{missing_id}/",
                )
        cards_to_read = [card_by_id[job_id] for job_id in visible_ids if job_id in card_by_id]
        for index, card in enumerate(cards_to_read, start=1):
            if index > 1:
                time.sleep(DETAIL_REQUEST_DELAY_SECONDS)
            job: LinkedInJob | None = None
            last_error: Exception | None = None
            attempts = 0
            read_outcome: dict[str, object] = {
                "linkedin_job_id": card.linkedin_job_id,
                "title": card.title or None,
                "status": "pending",
                "page_opened": False,
                "detail_opened": False,
                "description_read_status": "failed",
                "attempts": 0,
                "duration_ms": 0,
            }
            detail_started = time.perf_counter()
            for attempt in range(2):
                attempts = attempt + 1
                read_outcome["attempts"] = attempts
                try:
                    job = _read_job_detail(
                        context,
                        card,
                        require_complete_description=require_complete_descriptions,
                        outcome=read_outcome,
                    )
                    break
                except ManualLinkedInActionRequired:
                    raise
                except (LinkedInReadError, PlaywrightError) as error:
                    last_error = error
                    if attempt == 0:
                        time.sleep(DETAIL_RETRY_DELAY_SECONDS)
            duration_ms = max(0, int((time.perf_counter() - detail_started) * 1000))
            read_outcome["duration_ms"] = duration_ms
            if job is None:
                reason = str(last_error or "Dettaglio non leggibile.")
                unreadable.append({
                    "linkedin_job_id": card.linkedin_job_id,
                    "title": card.title,
                    "company": card.company,
                    "reason": reason,
                })
                read_outcome["status"] = "unreadable"
                read_outcome["reason"] = reason
                outcomes.append(read_outcome)
                LOGGER.warning(
                    "Could not read saved-job detail %d/%d (ID %s) after %d attempts; continuing.",
                    index, len(cards), card.linkedin_job_id, attempts,
                )
                continue
            if not job.title.strip():
                reason = "Descrizione letta, ma il titolo non era recuperabile dalla scheda né dal dettaglio."
                skipped_missing_title.append({
                    "linkedin_job_id": card.linkedin_job_id,
                    "title": None,
                    "company": job.company,
                    "reason": reason,
                })
                read_outcome["title"] = None
                read_outcome["status"] = "skipped_missing_title"
                read_outcome["reason"] = reason
                outcomes.append(read_outcome)
                LOGGER.warning(
                    "Saved job ID %s had a readable description but no recoverable title; not importing.",
                    card.linkedin_job_id,
                )
                continue
            results.append(job)
            read_outcome["status"] = "read"
            read_outcome["description_read_status"] = (
                "complete" if job.description_complete else "partial"
            )
            read_outcome["description_complete"] = bool(job.description_complete)
            outcomes.append(read_outcome)
            LOGGER.info(
                "Read saved job %d/%d (ID %s; description complete: %s; duration_ms: %d).",
                index, len(cards), job.linkedin_job_id, job.description_complete, duration_ms,
            )

        list_page.bring_to_front()
        return SavedJobCards(
            results,
            count_mismatch=getattr(cards, "count_mismatch", False),
            visible_ids=visible_ids,
            membership_verified=getattr(cards, "membership_verified", False),
            unreadable=unreadable,
            skipped_missing_title=skipped_missing_title,
            outcomes=outcomes,
        )


def read_one_saved_job(
    profile_dir: Path = DEFAULT_PROFILE_DIR,
) -> LinkedInJob:
    """Read only the first saved job and its visible description; store nothing."""
    jobs = _read_saved_jobs(
        profile_dir,
        limit=1,
        require_complete_descriptions=False,
    )
    if not jobs:
        raise LinkedInReadError("No saved LinkedIn jobs were found.")
    return jobs[0]


def read_all_saved_jobs(
    profile_dir: Path = DEFAULT_PROFILE_DIR,
) -> list[LinkedInJob]:
    """Read every saved job and require each visible description to be complete."""
    return _read_saved_jobs(
        profile_dir,
        limit=None,
        require_complete_descriptions=True,
    )


def read_new_saved_jobs(
    existing_job_ids: set[str],
    profile_dir: Path = DEFAULT_PROFILE_DIR,
) -> list[LinkedInJob]:
    """Read complete details only for newly saved jobs after the local boundary."""
    return _read_saved_jobs(
        profile_dir,
        limit=None,
        require_complete_descriptions=True,
        existing_job_ids={str(job_id) for job_id in existing_job_ids},
    )
