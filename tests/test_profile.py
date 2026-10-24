"""Tests for the local agent Markdown file and candidate CV handling."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from linkedin_connector.profile import (
    AgentPromptStore,
    CandidateProfileStore,
    LocalProfileError,
    extract_cv_text,
)


class LocalProfileTests(unittest.TestCase):
    def test_agent_roles_parse_from_markdown_and_guided_save_preserves_free_sections(self) -> None:
        source = ("# Shared policy\n\nShared rule stays here.\n\n## Internal roles and workflow\n\n"
                  "Intro text also stays.\n\n### 1. Researcher\n"
                  "<!-- agent:id=researcher phase=research enabled=true model=gpt-4.1-nano -->\n\n"
                  "Find official company information.\nSummarize relevant facts.\n\n"
                  "## Output format\n\nKeep the result concise.\n")
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AgentPromptStore(Path(temp_dir) / "AGENTS.md")
            store.save(source)
            agent = store.agents()[0]
            self.assertEqual(agent["name"], "Researcher")
            self.assertEqual(agent["model"], "gpt-4.1-nano")
            self.assertIn("official company", agent["summary"])
            self.assertEqual(agent["phase"], "Research")
            shared = store.general_instructions()
            self.assertIn("Shared rule stays here.", shared)
            self.assertNotIn("Find official company information", shared)

            changed = {**agent, "instructions": "Search official sources and cite URLs."}
            saved = store.replace_agents([changed])
            self.assertIn("Shared rule stays here.", saved)
            self.assertIn("Intro text also stays.", saved)
            self.assertIn("## Output format\n\nKeep the result concise.", saved)
            self.assertEqual(store.agents()[0]["instructions"], changed["instructions"])
            self.assertEqual(store.agents()[0]["model"], "gpt-4.1-nano")

    def test_feedback_rule_is_backed_up_and_reversibly_removed(self) -> None:
        markdown = ("# Rules\n\n## Internal roles and workflow\n\n### 1. Writer\n"
                    "<!-- agent:id=writer phase=writing enabled=true -->\n\nWrite evidence-based letters.\n")
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AgentPromptStore(Path(temp_dir) / "AGENTS.md")
            store.save(markdown)
            learned = store.append_learning_rule("writer", "Prefer concise opening paragraphs.")
            self.assertTrue(Path(learned["backup_path"]).is_file())
            self.assertIn(learned["rule"], store.agents()[0]["instructions"])
            store.remove_learning_rule("writer", learned["rule"])
            self.assertNotIn(learned["rule"], store.agents()[0]["instructions"])

    def test_agent_instructions_are_one_utf8_markdown_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AgentPromptStore(Path(temp_dir) / "AGENTS.md")
            saved = store.save("# Ricerca\n\n## Scrittura\n\n## Revisione")
            self.assertEqual(store.load(), saved)
            self.assertEqual(list(Path(temp_dir).glob("*.md")), [store.path])

    def test_agent_file_rejects_empty_and_oversized_instructions(self) -> None:
        store = AgentPromptStore(Path(tempfile.gettempdir()) / "not-written-agent.md")
        with self.assertRaises(LocalProfileError):
            store.save("  \n")
        with self.assertRaises(LocalProfileError) as error:
            store.save("x" * 100_001)
        self.assertEqual(error.exception.status, 413)

    def test_fresh_install_seeds_neutral_per_user_markdown_files_once(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        template_root = project_root / "templates" / "user_profile"
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir) / "user_profile"
            agents = AgentPromptStore(profile_dir / "AGENTS.md", template_root / "AGENTS.md.template")
            context = AgentPromptStore(
                profile_dir / "CANDIDATE_CONTEXT.md",
                template_root / "CANDIDATE_CONTEXT.md.template",
                label="file Markdown del contesto candidato",
            )
            self.assertIn("CANDIDATE_CONTEXT.md", agents.load())
            context_text = context.load()
            self.assertIn("Full name:", context_text)
            self.assertNotRegex(context_text, r"\b[\w.-]+@[\w.-]+\.\w+\b")

            context.save("# Context for this installation\n")
            AgentPromptStore(
                profile_dir / "CANDIDATE_CONTEXT.md", template_root / "CANDIDATE_CONTEXT.md.template"
            )
            self.assertEqual(context.load(), "# Context for this installation\n")

    def test_text_and_word_cv_are_extracted_without_altering_the_source(self) -> None:
        self.assertEqual(extract_cv_text("txt", b"\xef\xbb\xbfCandidate\r\nPython"), "Candidate\nPython")
        document = BytesIO()
        with zipfile.ZipFile(document, "w") as archive:
            archive.writestr(
                "word/document.xml",
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>Esperienza</w:t></w:r></w:p>"
                "<w:p><w:r><w:t>Analisi dati</w:t></w:r></w:p></w:body></w:document>",
            )
        self.assertEqual(extract_cv_text("docx", document.getvalue()), "Esperienza\nAnalisi dati")

    def test_candidate_profile_replaces_and_removes_only_its_cv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CandidateProfileStore(Path(temp_dir))
            self.assertFalse(store.status()["has_cv"])
            store.save("old.txt", "txt", b"Prima versione")
            status = store.save("new.md", "md", b"Seconda versione")
            self.assertEqual(status["format"], "md")
            self.assertEqual(status["filename"], "new.md")
            self.assertTrue(status["original_filename_available"])
            self.assertEqual(store.read_text(), "Seconda versione")
            files = list((Path(temp_dir) / "candidate_profile").iterdir())
            self.assertEqual({path.name for path in files}, {"cv.md", "cv-metadata.json"})
            store.metadata_path.unlink()
            legacy_status = store.status()
            self.assertEqual(legacy_status["filename"], "cv.md")
            self.assertFalse(legacy_status["original_filename_available"])
            store.delete()
            self.assertFalse(store.status()["has_cv"])

    def test_pdf_text_extraction_or_missing_dependency_error(self) -> None:
        try:
            from pypdf import PdfWriter
            from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        except ImportError:
            with self.assertRaises(LocalProfileError) as error:
                extract_cv_text("pdf", b"%PDF-1.7\nnot a complete PDF")
            self.assertEqual(error.exception.status, 501)
            return

        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        font_ref = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 72 720 Td (PDF CV text) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        pdf = BytesIO()
        writer.write(pdf)
        self.assertEqual(extract_cv_text("pdf", pdf.getvalue()), "PDF CV text")


if __name__ == "__main__":
    unittest.main()
