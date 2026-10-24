"""Provider research results are capped before page text enters generation."""
from __future__ import annotations

import json
import unittest

from linkedin_connector.ai_provider import OpenAIProvider


class ProviderResearchLimitTests(unittest.TestCase):
    def test_one_search_call_and_at_most_four_distinct_sites_reach_application(self):
        sources = [
            {"url": "https://www.example.com/about", "title": "Company"},
            {"url": "https://example.com/products", "title": "Same site"},
            {"url": "https://jobs.example.org/roles", "title": "Jobs"},
            {"url": "https://press.example.net/news", "title": "Press"},
            {"url": "https://investor.example.edu/company", "title": "Investors"},
            {"url": "https://other.example.io/about", "title": "Other"},
        ]
        facts = [
            {"claim": f"Verified item {index}.", "kind": "documented_fact", "source_urls": [item["url"]]}
            for index, item in enumerate(sources)
        ]
        response_value = {
            "status": "completed",
            "usage": {"input_tokens": 20, "output_tokens": 10},
            "output": [
                {"type": "web_search_call", "action": {"type": "search", "sources": sources}},
                {"type": "message", "content": [{"type": "output_text", "text": json.dumps({
                    "summary": "A sourced summary.", "facts": facts, "gaps": "",
                })}]},
            ],
        }
        requests = []

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def read(self, _): return json.dumps(response_value).encode()

        def transport(request, timeout):
            requests.append((request, timeout))
            return Response()

        provider = OpenAIProvider("sk-test", "gpt-4.1-mini", transport=transport)
        result = provider.research_company(
            job={"company": "Example", "location": "Chicago"},
            global_instructions="Not used for company research.",
        )

        payload = json.loads(requests[0][0].data)
        self.assertEqual(payload["max_tool_calls"], 1)
        self.assertEqual(payload["tools"], [{"type": "web_search", "search_context_size": "low"}])
        self.assertEqual(len(result["sources"]), 4)
        self.assertEqual(len({item["url"].split("/")[2].removeprefix("www.") for item in result["sources"]}), 4)
        retained_urls = {item["url"] for item in result["sources"]}
        self.assertTrue(all(url in retained_urls for fact in result["facts"] for url in fact["source_urls"]))
        self.assertEqual(len(result["facts"]), 4)
        self.assertEqual(result["search_calls"], 1)


if __name__ == "__main__":
    unittest.main()
