"""Bounded public-page text extraction; all network access is mocked or disabled."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from linkedin_connector.research_pages import (
    MAX_PAGE_TEXT_CHARS, MAX_RESEARCH_PAGES, _is_public_https_url, _visible_text,
    cached_research_pages, canonical_research_url, fetch_research_pages,
)


class ResearchPageTests(unittest.TestCase):
    def test_research_urls_dedupe_tracking_variants_and_fragments(self):
        first = canonical_research_url("https://www.Example.com/about/?utm_source=mail#team")
        second = canonical_research_url("https://example.com/about?fbclid=123")
        self.assertEqual(first, "https://example.com/about")
        self.assertEqual(first, second)

    def test_fetch_opens_at_most_four_distinct_hosts_and_reports_timing(self):
        requested = []

        class Response:
            headers = {"Content-Type": "text/plain; charset=utf-8"}

            def __enter__(self): return self
            def __exit__(self, *_): return None
            def read(self, _): return b"Official company information."

        class Opener:
            def open(self, request, timeout):
                requested.append((request.full_url, timeout))
                return Response()

        sources = [
            {"url": "https://www.example.com/about?utm_campaign=x", "title": "About"},
            {"url": "https://example.com/careers/about?ref=search", "title": "Same website"},
            {"url": "https://jobs.example.org/company", "title": "Jobs"},
            {"url": "https://press.example.net/news", "title": "Press"},
            {"url": "https://company.example.edu/about", "title": "Company"},
            {"url": "https://another.example.io/about", "title": "Another"},
        ]
        with patch("linkedin_connector.research_pages._is_public_https_url", return_value=True), \
             patch("linkedin_connector.research_pages.build_opener", return_value=Opener()):
            pages = fetch_research_pages(sources)

        self.assertEqual(MAX_RESEARCH_PAGES, 4)
        self.assertEqual(len(requested), 4)
        self.assertEqual(len(pages), 4)
        self.assertEqual(len({url.split('/')[2] for url, _ in requested}), 4)
        self.assertTrue(all(page["state"] == "READY" and page["cached"] is False for page in pages))
        self.assertTrue(all(type(page["duration_ms"]) is int and page["fetched_at"] for page in pages))

    def test_cached_research_pages_are_labeled_as_cache_hits(self):
        class Application:
            def get_research_pages(self, _job_id, _key):
                return [{"url": "https://example.com/about", "state": "READY", "text": "About"}]

            def save_research_pages(self, *_args):
                raise AssertionError("a cache hit must not refetch or rewrite pages")

        pages = cached_research_pages(
            Application(), "job-1",
            {"summary": "Company", "facts": [], "sources": [{"url": "https://example.com/about"}]},
        )
        self.assertTrue(pages[0]["cached"])
        self.assertEqual(pages[0]["text"], "About")

    def test_visible_text_strips_scripts_navigation_and_caps_page_text(self):
        html = ("<html><head><title>Company</title><script>secret command</script></head>"
                "<body><nav>Menu text</nav><main><h1>Company overview</h1>"
                "<p>We build products for healthcare teams.</p></main></body></html>").encode()
        text = _visible_text(html, "text/html; charset=utf-8")
        self.assertIn("Company overview", text)
        self.assertIn("We build products", text)
        self.assertNotIn("secret command", text)
        self.assertNotIn("Menu text", text)
        self.assertLessEqual(len(_visible_text((b"<p>" + b"x" * 20_000 + b"</p>"), "text/html")),
                             MAX_PAGE_TEXT_CHARS)

    def test_only_public_https_sources_are_considered_and_page_failures_are_nonfatal(self):
        self.assertFalse(_is_public_https_url("http://example.com/"))
        self.assertFalse(_is_public_https_url("https://localhost/"))
        self.assertEqual(fetch_research_pages([{"url": "http://example.com/"}]), [])
        with patch("linkedin_connector.research_pages._is_public_https_url", return_value=True), \
             patch("linkedin_connector.research_pages.build_opener", side_effect=OSError("offline")):
            pages = fetch_research_pages([{"url": "https://example.com/", "title": "Company"}])
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["state"], "UNAVAILABLE")
        self.assertEqual(pages[0]["text"], "")


if __name__ == "__main__":
    unittest.main()
