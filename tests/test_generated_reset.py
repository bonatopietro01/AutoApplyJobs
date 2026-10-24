from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sqlite3
from linkedin_connector.database import JobStore, JobStoreError
from linkedin_connector.generation_store import GenerationStore
from linkedin_connector.application_store import ApplicationStore
from linkedin_connector.generated_reset import reset_generated_letters
from tests.test_database import make_job

class ResetTests(unittest.TestCase):
    def test_reset_backups_outputs_and_preserves_jobs_research_usage_and_dormancy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = JobStore(root / "jobs.sqlite3")
            store.sync_jobs([make_job()])
            tasks = GenerationStore(store)
            ApplicationStore(store)
            tasks.reconcile()
            store.save_job_materials("123456789", cover_letter_text="Old generated letter")
            record = store.save_cover_letter_file("123456789", file_format="pdf", original_filename="letter.pdf", contents=b"pdf")
            with closing(store._connection()) as connection, connection:
                connection.execute("INSERT INTO cover_letter_knowledge(filename,file_format,content,extracted_text,created_at) VALUES ('style.txt','txt',X'61','style','now')")
                connection.execute("INSERT INTO company_research(linkedin_job_id,state,summary,updated_at) VALUES ('123456789','READY','verified research','now')")
                connection.execute("INSERT INTO api_operation_usage(model,operation,created_at) VALUES ('fixture','agent','now')")
                connection.execute("INSERT INTO ai_usage(linkedin_job_id,model,created_at) VALUES ('123456789','fixture','now')")
            result = reset_generated_letters(store, root / "backup")
            self.assertEqual(result["files"], 1)
            self.assertFalse((root / record["relative_path"]).exists())
            self.assertTrue((root / "backup" / record["relative_path"]).exists())
            with closing(store._connection()) as connection, connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM ai_usage").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT state FROM generation_tasks").fetchone()[0], "MANUAL")
                self.assertEqual(connection.execute("SELECT summary FROM company_research").fetchone()[0], "verified research")
                self.assertEqual(connection.execute("SELECT extracted_text FROM cover_letter_knowledge").fetchone()[0], "style")
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM api_operation_usage").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM job_materials").fetchone()[0], 0)
            self.assertEqual(tasks.reconcile(), 0)
            with closing(sqlite3.connect(root / "backup" / "jobs.sqlite3")) as connection:
                self.assertEqual(connection.execute("SELECT cover_letter_text FROM job_materials").fetchone()[0], "Old generated letter")

    def test_running_generation_prevents_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = JobStore(root / "jobs.sqlite3")
            store.sync_jobs([make_job()])
            tasks = GenerationStore(store)
            tasks.reconcile()
            with closing(store._connection()) as connection, connection:
                connection.execute("UPDATE generation_tasks SET state='RUNNING'")
            with self.assertRaises(JobStoreError):
                reset_generated_letters(store, root / "backup")
            self.assertFalse((root / "backup").exists())

    def test_external_file_reference_prevents_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = JobStore(root / "jobs.sqlite3")
            store.sync_jobs([make_job()])
            with closing(store._connection()) as connection, connection:
                connection.execute("INSERT INTO cover_letters(linkedin_job_id,version,file_format,relative_path,original_filename,created_at) VALUES ('123456789',1,'pdf','outside.pdf','outside.pdf','now')")
            with self.assertRaises(JobStoreError):
                reset_generated_letters(store, root / "backup")
            self.assertFalse((root / "backup").exists())

    def test_unlink_failure_rolls_back_database_and_restores_previous_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = JobStore(root / "jobs.sqlite3")
            store.sync_jobs([make_job()])
            GenerationStore(store).reconcile()
            store.save_job_materials("123456789", cover_letter_text="Old letter")
            one = store.save_cover_letter_file("123456789", file_format="pdf", original_filename="one.pdf", contents=b"one")
            two = store.save_cover_letter_file("123456789", file_format="docx", original_filename="two.docx", contents=b"two")
            original_unlink = Path.unlink
            def unlink(path, *args, **kwargs):
                if path.suffix == ".docx":
                    raise OSError("simulated file lock")
                return original_unlink(path, *args, **kwargs)
            with patch.object(Path, "unlink", unlink):
                with self.assertRaises(OSError):
                    reset_generated_letters(store, root / "backup")
            self.assertEqual((root / one["relative_path"]).read_bytes(), b"one")
            self.assertEqual((root / two["relative_path"]).read_bytes(), b"two")
            self.assertEqual(store.get_job_bundle("123456789")["materials"]["cover_letter_text"], "Old letter")
