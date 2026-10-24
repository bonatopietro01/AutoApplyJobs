"""Unit tests for the one-saved-job reader's deterministic parsing."""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from linkedin_connector.models import LinkedInJob
from linkedin_connector.reader import (
    LinkedInReadError,
    SavedJobCards,
    _description_from_text,
    _expand_description,
    _description_text,
    _ids_before_first_existing,
    _parse_expected_saved_count,
    _parse_job_card,
    _collect_saved_job_cards,
    _collect_saved_job_cards_pass,
    _has_explicit_empty_saved_state,
    _read_saved_jobs,
    _read_job_detail,
    _advance_saved_jobs_page,
)
from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError


class ParseJobCardTests(unittest.TestCase):
    def test_next_timeout_after_click_accepts_stable_new_full_ids_without_reclick(self):
        page = Mock()
        page.get_by_role.return_value.count.return_value = 1
        button = page.get_by_role.return_value.first
        button.is_enabled.return_value = True
        button.click.side_effect = PlaywrightTimeoutError("navigation timed out after click")
        with patch("linkedin_connector.reader._check_page_access"), patch(
                "linkedin_connector.reader._visible_job_ids", return_value=["1", "3"]):
            self.assertTrue(_advance_saved_jobs_page(page, ["1", "2"]))
        button.click.assert_called_once()

    def test_next_timeout_unchanged_list_is_partial_without_reclick(self):
        page = Mock()
        page.get_by_role.return_value.count.return_value = 1
        button = page.get_by_role.return_value.first
        button.is_enabled.return_value = True
        button.click.side_effect = PlaywrightTimeoutError("click timed out")
        with patch("linkedin_connector.reader._check_page_access"), patch(
                "linkedin_connector.reader._visible_job_ids", return_value=["1", "2"]):
            with self.assertRaises(LinkedInReadError):
                _advance_saved_jobs_page(page, ["1", "2"])
        button.click.assert_called_once()

    def test_next_unstable_changed_list_is_not_accepted(self):
        page = Mock()
        page.get_by_role.return_value.count.return_value = 1
        page.get_by_role.return_value.first.is_enabled.return_value = True
        with patch("linkedin_connector.reader._check_page_access"), patch(
                "linkedin_connector.reader._visible_job_ids", side_effect=[["3"], ["4"]] * 12):
            with self.assertRaises(LinkedInReadError):
                _advance_saved_jobs_page(page, ["1", "2"])

    def test_stable_counter_mismatch_is_reconcilable_after_two_complete_scans(self) -> None:
        cards = [LinkedInJob(str(index), "Role", "Acme", None,
                           f"https://www.linkedin.com/jobs/view/{index}/")
                 for index in range(35)]
        with patch("linkedin_connector.reader._collect_saved_job_cards_pass",
                   side_effect=[(cards, True), (list(reversed(cards)), True)]) as scan, \
             patch("linkedin_connector.reader._open_saved_jobs", return_value=36):
            result = _collect_saved_job_cards(object(), 36)
        self.assertEqual(scan.call_count, 2)
        self.assertEqual(len(result), 35)
        self.assertTrue(result.count_mismatch)
        self.assertTrue(result.membership_verified)

    def test_changing_cards_do_not_override_a_counter_mismatch(self) -> None:
        cards = [LinkedInJob(str(index), "Role", "Acme", None,
                           f"https://www.linkedin.com/jobs/view/{index}/")
                 for index in range(35)]
        changed = cards[:-1] + [LinkedInJob("999", "Role", "Acme", None,
                                          "https://www.linkedin.com/jobs/view/999/")]
        with patch("linkedin_connector.reader._collect_saved_job_cards_pass",
                   side_effect=[(cards, True), (changed, True)]), \
             patch("linkedin_connector.reader._open_saved_jobs", return_value=36):
            with self.assertRaises(LinkedInReadError):
                _collect_saved_job_cards(object(), 36)

    def test_counter_mismatch_resolved_on_retry_returns_reconcilable_list(self) -> None:
        cards = [LinkedInJob(str(index), "Role", "Acme", None,
                           f"https://www.linkedin.com/jobs/view/{index}/")
                 for index in range(36)]
        with patch("linkedin_connector.reader._collect_saved_job_cards_pass",
                   side_effect=[(cards[:-1], True), (cards, True)]), \
             patch("linkedin_connector.reader._open_saved_jobs", return_value=36):
            result = _collect_saved_job_cards(object(), 36)
        self.assertEqual(len(result), 36)
        self.assertFalse(result.count_mismatch)

    def test_stable_larger_counter_mismatch_is_reconcilable_after_two_complete_scans(self) -> None:
        cards = [LinkedInJob(str(index), "Role", "Acme", None,
                           f"https://www.linkedin.com/jobs/view/{index}/")
                 for index in range(34)]
        with patch("linkedin_connector.reader._collect_saved_job_cards_pass",
                   side_effect=[(cards, True), (cards, True)]), \
             patch("linkedin_connector.reader._open_saved_jobs", return_value=36):
            result = _collect_saved_job_cards(object(), 36)
        self.assertTrue(result.count_mismatch)
        self.assertTrue(result.membership_verified)
        self.assertEqual(len(result.visible_ids), 34)

    def test_explicit_empty_state_requires_empty_text_and_no_job_cards(self) -> None:
        class Node:
            def __init__(self, value=""):
                self.value = value
                self.first = self
            def count(self):
                return 0
            def is_enabled(self):
                return False
            def inner_text(self, **_kwargs):
                return self.value
        class Page:
            def __init__(self, text):
                self.text = text
            def locator(self, _selector):
                return Node(self.text)
            def get_by_role(self, *_args, **_kwargs):
                return Node()

        with patch("linkedin_connector.reader._visible_job_ids", return_value=[]):
            self.assertTrue(_has_explicit_empty_saved_state(Page("You have no saved jobs right now")))
            self.assertFalse(_has_explicit_empty_saved_state(Page("Tracker\nSalvate · 0")))

    def test_matching_counter_requires_verified_final_page(self) -> None:
        card = LinkedInJob("123", "Role", "Acme", None,
                          "https://www.linkedin.com/jobs/view/123/")
        with patch("linkedin_connector.reader._collect_saved_job_cards_pass",
                   return_value=([card], False)):
            with self.assertRaisesRegex(LinkedInReadError, "pagination was not verified"):
                _collect_saved_job_cards(object(), 1)

    def test_single_job_preview_does_not_require_full_page_ids(self) -> None:
        card = LinkedInJob("123", "Role", "Acme", None,
                           "https://www.linkedin.com/jobs/view/123/")

        def collect_partial(_page, _count, _limit, *, visible_ids_out):
            visible_ids_out.extend(["123", "456", "789"])
            return [card], False

        with patch("linkedin_connector.reader._collect_saved_job_cards_pass",
                   side_effect=collect_partial):
            result = _collect_saved_job_cards(object(), 36, limit=1)
        self.assertEqual([job.linkedin_job_id for job in result], ["123"])
        self.assertFalse(result.membership_verified)

    def test_zero_counter_without_verified_empty_page_does_not_reconcile(self) -> None:
        with patch("linkedin_connector.reader._collect_saved_job_cards_pass",
                   return_value=([], False)):
            with self.assertRaisesRegex(LinkedInReadError, "explicit empty list"):
                _collect_saved_job_cards(object(), 0)

    def test_zero_counter_with_unreadable_visible_card_is_not_empty(self) -> None:
        page = object()

        def visible_card(_page, _count, _limit, *, visible_ids_out):
            visible_ids_out.append("123")
            return [], True

        with patch("linkedin_connector.reader._collect_saved_job_cards_pass",
                   side_effect=visible_card), \
             patch("linkedin_connector.reader._open_saved_jobs", return_value=0):
            with self.assertRaisesRegex(LinkedInReadError, "explicit empty list"):
                _collect_saved_job_cards(page, 0)

    def test_explicit_empty_saved_list_is_confirmed_by_two_complete_scans(self) -> None:
        page = object()
        with patch("linkedin_connector.reader._collect_saved_job_cards_pass",
                   side_effect=[([], True), ([], True)]) as scan, \
             patch("linkedin_connector.reader._open_saved_jobs", return_value=0) as reopen:
            result = _collect_saved_job_cards(page, 0)
        self.assertEqual(scan.call_count, 2)
        reopen.assert_called_once_with(page)
        self.assertEqual(result.visible_ids, [])
        self.assertFalse(result.count_mismatch)
        self.assertTrue(result.membership_verified)

    def test_empty_verified_envelope_survives_full_reader(self):
        from unittest.mock import MagicMock
        envelope = SavedJobCards([], visible_ids=[], membership_verified=True)
        with patch("linkedin_connector.reader.persistent_browser_context", return_value=MagicMock()), \
             patch("linkedin_connector.reader._linkedin_page", return_value=Mock()), \
             patch("linkedin_connector.reader._open_saved_jobs", return_value=0), \
             patch("linkedin_connector.reader._collect_saved_job_cards", return_value=envelope):
            result = _read_saved_jobs(Path("profile"), limit=None, require_complete_descriptions=True)
        self.assertIs(result, envelope)
        self.assertTrue(result.membership_verified)

    def test_unreadable_detail_does_not_stop_reading_later_cards(self) -> None:
        first = LinkedInJob("111", "Unreadable role", "Acme", None,
                            "https://www.linkedin.com/jobs/view/111/")
        second = LinkedInJob("222", "Readable role", "Beta", None,
                             "https://www.linkedin.com/jobs/view/222/")
        listed = SavedJobCards(
            [first, second], visible_ids=["111", "222"], membership_verified=True
        )

        class Context:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def new_page(self):
                return Mock()

        list_page = Mock()
        readable = LinkedInJob("222", "Readable role", "Beta", None,
                               "https://www.linkedin.com/jobs/view/222/",
                               "Complete description", True)
        with patch("linkedin_connector.reader.persistent_browser_context", return_value=Context()), \
             patch("linkedin_connector.reader._linkedin_page", return_value=list_page), \
             patch("linkedin_connector.reader._open_saved_jobs", return_value=2), \
             patch("linkedin_connector.reader._collect_saved_job_cards", return_value=listed), \
             patch("linkedin_connector.reader._read_job_detail",
                   side_effect=[LinkedInReadError("Description unavailable"),
                                LinkedInReadError("Description unavailable"), readable]), \
             patch("linkedin_connector.reader.time.sleep"):
            result = _read_saved_jobs(
                Path("profile"), limit=None, require_complete_descriptions=True
            )

        self.assertEqual([job.linkedin_job_id for job in result], ["222"])
        self.assertEqual(result.visible_ids, ["111", "222"])
        self.assertTrue(result.membership_verified)
        self.assertEqual(result.unreadable[0]["linkedin_job_id"], "111")
        self.assertEqual(result.unreadable[0]["reason"], "Description unavailable")
        outcomes = {item["linkedin_job_id"]: item for item in result.outcomes}
        self.assertEqual(outcomes["111"]["status"], "unreadable")
        self.assertEqual(outcomes["111"]["attempts"], 2)
        self.assertEqual(outcomes["111"]["description_read_status"], "failed")
        self.assertGreaterEqual(outcomes["111"]["duration_ms"], 0)
        self.assertEqual(outcomes["222"]["status"], "read")
        self.assertEqual(outcomes["222"]["description_read_status"], "complete")
        self.assertGreaterEqual(outcomes["222"]["duration_ms"], 0)

    def test_visible_card_without_title_opens_detail_and_is_separated_from_unreadable(self):
        envelope = SavedJobCards([], visible_ids=["333"], membership_verified=True)

        class Context:
            pages = []
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False

        list_page = Mock()
        description_read_without_title = LinkedInJob(
            "333", "", None, None, "https://www.linkedin.com/jobs/view/333/",
            "Complete description", True,
        )

        def read_detail(_context, _job, *, outcome, **_kwargs):
            outcome["description_read_status"] = "complete"
            outcome["detail_opened"] = True
            return description_read_without_title

        with patch("linkedin_connector.reader.persistent_browser_context", return_value=Context()), \
             patch("linkedin_connector.reader._linkedin_page", return_value=list_page), \
             patch("linkedin_connector.reader._open_saved_jobs", return_value=1), \
             patch("linkedin_connector.reader._collect_saved_job_cards", return_value=envelope), \
             patch("linkedin_connector.reader._read_job_detail",
                   side_effect=read_detail) as detail, \
             patch("linkedin_connector.reader.time.sleep"):
            result = _read_saved_jobs(Path("profile"), limit=None,
                                      require_complete_descriptions=True)
        detail.assert_called_once()
        placeholder = detail.call_args.args[1]
        self.assertEqual(placeholder.title, "")
        self.assertEqual(placeholder.linkedin_url, "https://www.linkedin.com/jobs/view/333/")
        self.assertEqual(result.unreadable, [])
        self.assertEqual(result.skipped_missing_title[0]["linkedin_job_id"], "333")
        self.assertEqual(result.outcomes[0]["status"], "skipped_missing_title")
        self.assertEqual(result.outcomes[0]["description_read_status"], "complete")

    def test_detail_recovers_missing_card_title_from_single_visible_h1_before_reading_description(self):
        class Heading:
            def is_visible(self):
                return True

            def inner_text(self, **_kwargs):
                return "Senior Data Analyst"

        class Headings:
            count = lambda self: 1
            first = Heading()

        class Main:
            def locator(self, selector):
                if selector == "h1":
                    return Headings()
                raise AssertionError(f"unexpected selector {selector}")

        class DetailPage:
            url = "https://www.linkedin.com/jobs/view/333/"

            def goto(self, *_args, **_kwargs):
                return None

            def locator(self, selector):
                if selector == "main":
                    return Main()
                raise AssertionError(f"unexpected selector {selector}")

            def close(self):
                pass

        class Context:
            def new_page(self):
                return DetailPage()

        job = LinkedInJob("333", "", None, None, "https://www.linkedin.com/jobs/view/333/")
        outcome = {}
        with patch("linkedin_connector.reader._check_page_access"), \
             patch("linkedin_connector.reader._ensure_signed_in"), \
             patch("linkedin_connector.reader._description_text",
                   return_value=("Complete description", True)) as read_description:
            result = _read_job_detail(
                Context(), job, require_complete_description=True, outcome=outcome
            )

        self.assertEqual(result.title, "Senior Data Analyst")
        self.assertEqual(result.description, "Complete description")
        self.assertTrue(outcome["detail_opened"])
        self.assertEqual(outcome["title"], "Senior Data Analyst")
        read_description.assert_called_once()

    def test_visible_ids_safety_limit_includes_unreadable_cards(self):
        for output in (None, []):
            with self.subTest(output=output), patch(
                    "linkedin_connector.reader._visible_job_ids",
                    return_value=[str(index) for index in range(101)]), patch(
                    "linkedin_connector.reader._collect_current_page") as parse:
                with self.assertRaisesRegex(LinkedInReadError, "100-job safety limit"):
                    _collect_saved_job_cards_pass(Mock(), None, visible_ids_out=output)
                parse.assert_not_called()

    def test_full_scan_continues_past_reported_count_until_pagination_ends(self) -> None:
        scanned = []

        def collect_page(_page, found, _limit):
            job_id = str(len(scanned) + 1)
            scanned.append(job_id)
            found[job_id] = LinkedInJob(job_id, "Role", "Acme", None,
                                      f"https://www.linkedin.com/jobs/view/{job_id}/")

        with patch("linkedin_connector.reader._visible_job_ids", side_effect=[["1"], ["2"]]), \
             patch("linkedin_connector.reader._collect_current_page", side_effect=collect_page), \
             patch("linkedin_connector.reader._advance_saved_jobs_page", side_effect=[True, False]):
            cards, reached_last_page = _collect_saved_job_cards_pass(Mock(), 1)
        self.assertEqual([job.linkedin_job_id for job in cards], ["1", "2"])
        self.assertTrue(reached_last_page)

    def test_parses_one_linkedin_job_card(self) -> None:
        job = _parse_job_card(
            "https://www.linkedin.com/jobs/view/4471694286/",
            [
                "Data Science & Process Modeling Intern",
                "Johnson & Johnson MedTech · Sciaffusa (Ibrido)",
                "Pubblicata 1g fa",
            ],
        )

        self.assertEqual(job.linkedin_job_id, "4471694286")
        self.assertEqual(job.title, "Data Science & Process Modeling Intern")
        self.assertEqual(job.company, "Johnson & Johnson MedTech")
        self.assertEqual(job.location, "Sciaffusa (Ibrido)")
        self.assertEqual(
            job.linkedin_url,
            "https://www.linkedin.com/jobs/view/4471694286/",
        )

    def test_rejects_link_without_a_stable_job_id(self) -> None:
        with self.assertRaises(LinkedInReadError):
            _parse_job_card("/jobs/collections/", ["Some job"])

    def test_allows_card_without_title_for_detail_page_recovery(self) -> None:
        job = _parse_job_card("/jobs/view/123456789/", ["", "Acme · Remote"])
        self.assertEqual(job.linkedin_job_id, "123456789")
        self.assertEqual(job.title, "")
        self.assertIsNone(job.company)

    def test_parses_saved_counter_from_tracker_header(self) -> None:
        self.assertEqual(_parse_expected_saved_count("Tracker\nSalvate · 33\nIn corso · 3"), 33)
        self.assertEqual(_parse_expected_saved_count("Saved 7\nApplied 2"), 7)
        self.assertIsNone(_parse_expected_saved_count("Tracker\nArchiviate"))

    def test_incremental_boundary_stops_at_first_job_already_in_database(self) -> None:
        new_ids, reached_existing = _ids_before_first_existing(
            ["900", "901", "901", "123456789", "902"], {"123456789", "456"}
        )

        self.assertEqual(new_ids, ["900", "901"])
        self.assertTrue(reached_existing)

    def test_incremental_scan_without_known_jobs_keeps_all_visible_ids(self) -> None:
        new_ids, reached_existing = _ids_before_first_existing(["900", "901"], set())

        self.assertEqual(new_ids, ["900", "901"])
        self.assertFalse(reached_existing)

    def test_description_is_incomplete_while_show_more_control_remains(self) -> None:
        description, complete = _description_from_text(
            "Informazioni sull’offerta di lavoro\n\nVisible text\n… altro\n"
            "Imposta un avviso per offerte di lavoro simili"
        )

        self.assertEqual(description, "Visible text")
        self.assertFalse(complete)

    def test_expanded_description_stops_at_the_next_section(self) -> None:
        description, complete = _description_from_text(
            "Informazioni sull’offerta di lavoro\n\nFull visible text\n"
            "Imposta un avviso per offerte di lavoro simili\nOther page content"
        )

        self.assertEqual(description, "Full visible text")
        self.assertTrue(complete)

    def test_description_parser_accepts_linkedin_heading_variants(self) -> None:
        description, complete = _description_from_text(
            "Job description\n\nBuild reporting tools for the operations team."
        )
        self.assertEqual(description, "Build reporting tools for the operations team.")
        self.assertTrue(complete)

        description, complete = _description_from_text(
            "Informazioni sull'offerta di lavoro\n\nAnalizzare processi e dati aziendali."
        )
        self.assertEqual(description, "Analizzare processi e dati aziendali.")
        self.assertTrue(complete)

    def test_description_reader_falls_back_to_linkedin_description_container(self) -> None:
        class Node:
            def __init__(self, text=""):
                self.text = text
                self.first = self

            def wait_for(self, **_kwargs):
                return None

            def inner_text(self, **_kwargs):
                return self.text

            def is_visible(self):
                return True

            def count(self):
                return 1

            def nth(self, _index):
                return self

            def filter(self, **_kwargs):
                return Collection([])

            def locator(self, selector):
                if selector == "h1, h2, h3":
                    return Collection([])
                if "jobs-description-content__text" in selector:
                    return Collection([Node("Build dashboards and improve reporting workflows.")])
                return Collection([])

        class Collection:
            def __init__(self, items):
                self.items = items
                self.first = items[0] if items else Node()

            def count(self):
                return len(self.items)

            def nth(self, index):
                return self.items[index]

            def filter(self, **_kwargs):
                return self

            def locator(self, selector):
                return self.first.locator(selector)

            def wait_for(self, **kwargs):
                return self.first.wait_for(**kwargs)

            def inner_text(self, **kwargs):
                return self.first.inner_text(**kwargs)

            def is_visible(self):
                return self.first.is_visible()

        class Page:
            def __init__(self):
                self.main = Node("Job title and company details, but no recognized heading.")

            def locator(self, selector):
                return Collection([self.main]) if selector == "main" else Collection([])

        description, complete = _description_text(Page())
        self.assertEqual(description, "Build dashboards and improve reporting workflows.")
        self.assertTrue(complete)

    def test_description_text_polls_briefly_for_late_rendered_description(self) -> None:
        class Empty:
            first = None

            def filter(self, **_kwargs):
                return self

            def count(self):
                return 0

        class Main:
            first = None

            def __init__(self, page):
                self.first = self
                self.page = page

            def wait_for(self, **_kwargs):
                return None

            def locator(self, _selector):
                return Empty()

            def inner_text(self, **_kwargs):
                if self.page.wait_count == 0:
                    return "Job title and company details"
                return "About the job\nBuild dashboards and improve reporting workflows."

        class Page:
            wait_count = 0

            def __init__(self):
                self.main = Main(self)

            def locator(self, selector):
                if selector == "main":
                    return self.main
                raise AssertionError(f"unexpected selector {selector}")

            def wait_for_timeout(self, _milliseconds):
                self.wait_count += 1

        page = Page()
        description, complete = _description_text(page)
        self.assertEqual(description, "Build dashboards and improve reporting workflows.")
        self.assertTrue(complete)
        self.assertEqual(page.wait_count, 1)

    def test_description_expansion_retries_with_scoped_forced_click_after_overlay(self) -> None:
        class Collection:
            def __init__(self, items=()):
                self.items = list(items)
                self.first = self.items[0] if self.items else None

            def filter(self, **_kwargs):
                return self

            def count(self):
                return len(self.items)

            def nth(self, index):
                return self.items[index]

        class Page:
            def __init__(self):
                self.expanded = False
                self.forced = False
                self.heading = Heading(self)
                self.control = Control(self)

            @property
            def first(self):
                return self

            def locator(self, selector):
                if selector == "h1, h2, h3":
                    return Collection([self.heading])
                if selector == "button":
                    return Collection([self.control])
                return Collection()

            def inner_text(self, **_kwargs):
                tail = "Full visible job description" if self.expanded else "Visible text\n... more"
                return f"About the job\n{tail}\nSet alert for similar jobs"

            def wait_for_timeout(self, _milliseconds):
                return None

        class Heading:
            def __init__(self, page):
                self.page = page

            def locator(self, _selector):
                return Scope(self.page)

        class Scope:
            def __init__(self, page):
                self.page = page

            def locator(self, selector):
                if selector == "xpath=..":
                    return self
                if selector == "button":
                    return Collection([self.page.control])
                return Collection()

        class Control:
            def __init__(self, page):
                self.page = page

            def is_visible(self):
                return True

            def scroll_into_view_if_needed(self, **_kwargs):
                return None

            def click(self, *, force=False, **_kwargs):
                if not force:
                    raise PlaywrightError("intercepted by overlay")
                self.page.forced = True
                self.page.expanded = True

        page = Page()
        self.assertTrue(_expand_description(page, page))
        self.assertTrue(page.forced)


if __name__ == "__main__":
    unittest.main()
