"""Bounded, read-only extraction of cited public company pages for local analysis."""
from __future__ import annotations

import ipaddress
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_RESEARCH_PAGES = 4
MAX_RESPONSE_BYTES = 750_000
MAX_PAGE_TEXT_CHARS = 6_000
FETCH_TIMEOUT_SECONDS = 3
_TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}
_BLOCKED_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form", "button"}
_BLOCK_TAGS = {"article", "br", "div", "h1", "h2", "h3", "h4", "li", "main", "p", "section"}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._blocked = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in _BLOCKED_TAGS:
            self._blocked += 1
        elif not self._blocked and tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in _BLOCKED_TAGS:
            self._blocked = max(0, self._blocked - 1)
        elif not self._blocked and tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._blocked:
            self.parts.append(data)


def canonical_research_url(url: str) -> str:
    """Normalize a research URL for deduplication without changing its page path."""
    try:
        parsed = urlsplit(str(url).strip())
        host = (parsed.hostname or "").rstrip(".").casefold()
        if parsed.scheme.casefold() != "https" or not host or parsed.username or parsed.password:
            return ""
        if host.startswith("www."):
            host = host[4:]
        port = parsed.port
        netloc = host if port in (None, 443) else f"{host}:{port}"
        path = parsed.path or "/"
        if path != "/":
            path = path.rstrip("/") or "/"
        query_items = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            folded = key.casefold()
            if folded.startswith("utm_") or folded in _TRACKING_QUERY_KEYS:
                continue
            query_items.append((key, value))
        query = urlencode(sorted(query_items), doseq=True)
        return urlunsplit(("https", netloc, path, query, ""))
    except (TypeError, ValueError, UnicodeError):
        return ""


def research_site_key(url: str) -> str:
    """Return a host-level key so one result per website counts toward the cap."""
    normalized = canonical_research_url(url)
    if not normalized:
        return ""
    host = (urlsplit(normalized).hostname or "").casefold()
    return host[4:] if host.startswith("www.") else host


def _is_public_https_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").casefold()
        if (parsed.scheme.casefold() != "https" or not host or parsed.username or parsed.password or
                parsed.port not in (None, 443) or host == "localhost" or host.endswith((".localhost", ".local", ".internal"))):
            return False
        try:
            address = ipaddress.ip_address(host)
            return address.is_global
        except ValueError:
            if "." not in host:
                return False
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(item[4][0]).is_global for item in addresses)
    except (OSError, ValueError, UnicodeError):
        return False


def _visible_text(body: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset\s*=\s*[\"']?([^;\"'\s]+)", content_type, re.IGNORECASE)
    encoding = charset_match.group(1) if charset_match else "utf-8"
    try:
        decoded = body.decode(encoding, errors="replace")
    except LookupError:
        decoded = body.decode("utf-8", errors="replace")
    parser = _VisibleText()
    parser.feed(decoded)
    raw = "".join(parser.parts).replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", line).strip() for line in raw.splitlines()]
    compact: list[str] = []
    for line in lines:
        if line and (not compact or compact[-1].casefold() != line.casefold()):
            compact.append(line)
    return "\n\n".join(compact)[:MAX_PAGE_TEXT_CHARS]


def _fetch_page(source: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    url = str(source.get("url") or "")[:2_000]
    title = str(source.get("title") or url)[:300]
    result = {"url": url, "title": title, "state": "UNAVAILABLE", "error_code": "fetch_failed", "text": ""}
    try:
        if not _is_public_https_url(url):
            result.update(state="SKIPPED", error_code="unsafe_or_non_https_url")
            return result
        request = Request(url, headers={
            "User-Agent": "LinkedInJobConnector/1.0 (read-only public company research)",
            "Accept": "text/html,text/plain;q=0.9",
        })
        response = build_opener(_NoRedirect()).open(request, timeout=FETCH_TIMEOUT_SECONDS)
        with response:
            content_type = str(response.headers.get("Content-Type", "")).casefold()
            if not ("text/html" in content_type or "text/plain" in content_type):
                result.update(state="SKIPPED", error_code="unsupported_content_type")
                return result
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                result.update(state="SKIPPED", error_code="page_too_large")
                return result
        text = _visible_text(body, content_type)
        if not text:
            result.update(state="SKIPPED", error_code="empty_page")
            return result
        result.update(state="READY", error_code="", text=text)
        return result
    except HTTPError as error:
        result.update(state="SKIPPED" if 300 <= error.code < 400 else "UNAVAILABLE",
                      error_code="redirect_not_followed" if 300 <= error.code < 400 else "http_error")
    except (OSError, URLError, TimeoutError, ValueError):
        result.update(state="UNAVAILABLE", error_code="fetch_failed")
    finally:
        result["duration_ms"] = max(0, min(int((time.monotonic() - started) * 1000), 120_000))
        result["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return result


def fetch_research_pages(sources: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Fetch at most four distinct cited websites; redirects are not followed."""
    selected: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_sites: set[str] = set()
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        url = source.get("url")
        if not isinstance(url, str):
            continue
        canonical_url = canonical_research_url(url)
        site_key = research_site_key(url)
        if not canonical_url or canonical_url in seen_urls or site_key in seen_sites:
            continue
        seen_urls.add(canonical_url)
        seen_sites.add(site_key)
        selected.append({"url": url, "title": source.get("title", "")})
        if len(selected) >= MAX_RESEARCH_PAGES:
            break
    if not selected:
        return []
    # Small concurrency bound keeps page reads from serially adding four network timeouts.
    with ThreadPoolExecutor(max_workers=min(len(selected), MAX_RESEARCH_PAGES)) as executor:
        return [{**page, "cached": False} for page in executor.map(_fetch_page, selected)]


def research_page_cache_key(research: dict[str, Any]) -> str:
    """Identify one saved research result without storing page contents in its metadata."""
    import hashlib
    import json

    value = [research.get("job_hash"), research.get("updated_at"), research.get("summary"),
             research.get("facts"),
             [str(item.get("url") or "") for item in research.get("sources", []) if isinstance(item, dict)]]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def cached_research_pages(application: Any, job_id: str, research: dict[str, Any]) -> list[dict[str, Any]]:
    """Reuse extracted source text for this exact saved research version."""
    key = research_page_cache_key(research)
    cached = application.get_research_pages(job_id, key)
    if cached is not None:
        return [{**page, "cached": True} for page in cached]
    pages = fetch_research_pages(research.get("sources"))
    application.save_research_pages(job_id, key, pages)
    return pages


def page_excerpts(page: dict[str, Any], *, limit: int = 10, max_chars: int = 750) -> list[str]:
    """Split already-extracted visible text into short, source-preserving scoring units."""
    if page.get("state") != "READY":
        return []
    paragraphs = [re.sub(r"\s+", " ", part).strip()
                  for part in re.split(r"\n\s*\n|\n", str(page.get("text") or ""))]
    output: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            if len(paragraph) >= 45:
                output.append(paragraph)
        else:
            start = 0
            while start < len(paragraph) and len(output) < limit:
                end = min(start + max_chars, len(paragraph))
                if end < len(paragraph):
                    boundary = paragraph.rfind(" ", start + int(max_chars * .65), end)
                    if boundary > start:
                        end = boundary
                output.append(paragraph[start:end].strip())
                start = end
        if len(output) >= limit:
            break
    return output[:limit]
