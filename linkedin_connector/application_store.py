"""Local research, chat, feedback, cover-letter references, and revisions."""

from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

from linkedin_connector.database import JobStore, JobStoreError
from linkedin_connector.research_pages import MAX_RESEARCH_PAGES, research_site_key


MAX_KNOWLEDGE_FILES = 10
MAX_KNOWLEDGE_FILE_BYTES = 8 * 1024 * 1024
MAX_KNOWLEDGE_TEXT_CHARS = 30_000
MAX_CHAT_MESSAGES_PER_JOB = 200
MAX_CHAT_TEXT_CHARS = 8_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _company_cache_key(company_name: str) -> str:
    normalized = unicodedata.normalize("NFKC", company_name).casefold()
    normalized = " ".join(re.findall(r"[\w]+", normalized, flags=re.UNICODE))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def _safe_company_research(research: dict[str, Any]) -> dict[str, Any] | None:
    """Keep only company claims backed by one of the recorded web sources."""
    summary = str(research.get("summary") or "").strip()
    raw_sources = research.get("sources") or []
    if not summary or not isinstance(raw_sources, list):
        return None
    sources = []
    for source in raw_sources[:30]:
        if (isinstance(source, dict) and isinstance(source.get("url"), str) and
                source["url"].startswith(("https://", "http://")) and
                isinstance(source.get("title", ""), str)):
            sources.append({"url": source["url"][:2_000], "title": source.get("title", "")[:300]})
    if not sources:
        return None
    allowed_urls = {source["url"] for source in sources}
    facts = []
    raw_facts = research.get("facts") or []
    if isinstance(raw_facts, list):
        for fact in raw_facts[:30]:
            if (not isinstance(fact, dict) or not isinstance(fact.get("claim"), str) or
                    fact.get("kind") not in {"documented_fact", "interpretation"}):
                continue
            raw_urls = fact.get("source_urls", [])
            if not isinstance(raw_urls, list):
                continue
            urls = [url for url in raw_urls
                    if isinstance(url, str) and url in allowed_urls][:10]
            if urls:
                facts.append({"claim": fact["claim"][:1_500], "kind": fact["kind"], "source_urls": urls})
    try:
        search_calls = max(0, min(int(research.get("search_calls") or 0), 100))
    except (TypeError, ValueError):
        search_calls = 0
    return {
        "summary": summary[:20_000],
        "sources": sources,
        "facts": facts,
        "gaps": str(research.get("gaps") or "")[:5_000],
        "search_calls": search_calls,
    }


def _upsert_company_profile(connection: sqlite3.Connection, company_name: str,
                             research: dict[str, Any], researched_at: str) -> bool:
    key = _company_cache_key(company_name)
    name = " ".join(str(company_name or "").split())[:300]
    safe = _safe_company_research(research)
    if not key or not name or not safe:
        return False
    connection.execute(
        """INSERT INTO company_profiles
           (company_key,company_name,summary,sources_json,facts_json,gaps,search_calls,
            researched_at,last_used_at,use_count)
           VALUES (?,?,?,?,?,?,?,?,?,0)
           ON CONFLICT(company_key) DO UPDATE SET company_name=excluded.company_name,
           summary=excluded.summary,sources_json=excluded.sources_json,facts_json=excluded.facts_json,
           gaps=excluded.gaps,search_calls=excluded.search_calls,researched_at=excluded.researched_at,
           last_used_at=excluded.last_used_at,use_count=0
           WHERE excluded.researched_at >= company_profiles.researched_at""",
        (key, name, safe["summary"], json.dumps(safe["sources"], ensure_ascii=False),
         json.dumps(safe["facts"], ensure_ascii=False), safe["gaps"], safe["search_calls"],
         researched_at or _now(), researched_at or _now()),
    )
    return True


class ApplicationStore:
    """Local application and reusable research storage in the app's SQLite database."""

    def __init__(self, store: JobStore) -> None:
        self.store = store
        connection = store._connection()
        try:
            connection.executescript(
                """CREATE TABLE IF NOT EXISTS company_research (
                    linkedin_job_id TEXT PRIMARY KEY REFERENCES jobs(linkedin_job_id) ON DELETE CASCADE,
                    state TEXT NOT NULL CHECK (state IN ('READY','UNAVAILABLE')),
                    summary TEXT NOT NULL DEFAULT '',
                    job_hash TEXT NOT NULL DEFAULT '',
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    facts_json TEXT NOT NULL DEFAULT '[]',
                    gaps TEXT NOT NULL DEFAULT '',
                    search_calls INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    researched_at TEXT NOT NULL DEFAULT '',
                    error_code TEXT
                );
                CREATE TABLE IF NOT EXISTS company_research_cache (
                    company_key TEXT PRIMARY KEY,
                    company_name TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    facts_json TEXT NOT NULL DEFAULT '[]',
                    gaps TEXT NOT NULL DEFAULT '',
                    search_calls INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    use_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS company_profiles (
                    company_key TEXT PRIMARY KEY,
                    company_name TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    facts_json TEXT NOT NULL DEFAULT '[]',
                    gaps TEXT NOT NULL DEFAULT '',
                    search_calls INTEGER NOT NULL DEFAULT 0,
                    researched_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    use_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_company_profiles_name
                    ON company_profiles(company_name COLLATE NOCASE);
                CREATE TABLE IF NOT EXISTS company_research_pages (
                    linkedin_job_id TEXT PRIMARY KEY REFERENCES jobs(linkedin_job_id) ON DELETE CASCADE,
                    research_hash TEXT NOT NULL,
                    pages_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS job_chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    linkedin_job_id TEXT NOT NULL REFERENCES jobs(linkedin_job_id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK (role IN ('user','assistant')),
                    content TEXT NOT NULL,
                    proposal_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_job_chat_job_id
                    ON job_chat_messages(linkedin_job_id, id);
                CREATE TABLE IF NOT EXISTS letter_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    linkedin_job_id TEXT NOT NULL REFERENCES jobs(linkedin_job_id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    letter_text TEXT NOT NULL,
                    source TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0 CHECK (active IN (0,1)),
                    created_at TEXT NOT NULL,
                    UNIQUE(linkedin_job_id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_letter_versions_job
                    ON letter_versions(linkedin_job_id, version DESC);
                CREATE TABLE IF NOT EXISTS cover_letter_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    linkedin_job_id TEXT NOT NULL REFERENCES jobs(linkedin_job_id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 10),
                    comment TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    learning_rule TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS cover_letter_knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    file_format TEXT NOT NULL CHECK (file_format IN ('pdf','docx','txt','md')),
                    content BLOB NOT NULL,
                    extracted_text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_learning (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    rule TEXT NOT NULL,
                    backup_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );"""
            )
            research_columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(company_research)")}
            if "job_hash" not in research_columns:
                connection.execute("ALTER TABLE company_research ADD COLUMN job_hash TEXT NOT NULL DEFAULT ''")
            if "facts_json" not in research_columns:
                connection.execute("ALTER TABLE company_research ADD COLUMN facts_json TEXT NOT NULL DEFAULT '[]'")
            if "gaps" not in research_columns:
                connection.execute("ALTER TABLE company_research ADD COLUMN gaps TEXT NOT NULL DEFAULT ''")
            if "cache_scope" not in research_columns:
                connection.execute("ALTER TABLE company_research ADD COLUMN cache_scope TEXT NOT NULL DEFAULT 'job'")
            if "researched_at" not in research_columns:
                connection.execute("ALTER TABLE company_research ADD COLUMN researched_at TEXT NOT NULL DEFAULT ''")
            self._migrate_company_profiles(connection)
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _migrate_company_profiles(connection: sqlite3.Connection) -> None:
        """Import legacy expiring-cache and per-job research into the durable company database."""
        records: list[tuple[str, dict[str, Any], str]] = []
        for row in connection.execute("SELECT * FROM company_research_cache ORDER BY updated_at"):
            try:
                research = {
                    "summary": row["summary"],
                    "sources": json.loads(row["sources_json"] or "[]"),
                    "facts": json.loads(row["facts_json"] or "[]"),
                    "gaps": row["gaps"],
                    "search_calls": row["search_calls"],
                }
            except (ValueError, TypeError):
                continue
            records.append((str(row["company_name"]), research, str(row["updated_at"] or _now())))
        for row in connection.execute(
                """SELECT j.company AS company_name,r.summary,r.sources_json,r.facts_json,r.gaps,
                          r.search_calls,r.updated_at
                   FROM company_research r JOIN jobs j ON j.linkedin_job_id=r.linkedin_job_id
                   WHERE r.state='READY' AND j.company IS NOT NULL AND trim(j.company)!=''
                   ORDER BY r.updated_at"""):
            try:
                research = {
                    "summary": row["summary"],
                    "sources": json.loads(row["sources_json"] or "[]"),
                    "facts": json.loads(row["facts_json"] or "[]"),
                    "gaps": row["gaps"],
                    "search_calls": row["search_calls"],
                }
            except (ValueError, TypeError):
                continue
            records.append((str(row["company_name"]), research, str(row["updated_at"] or _now())))
        for company_name, research, researched_at in records:
            _upsert_company_profile(connection, company_name, research, researched_at)

    def save_research(self, job_id: str, *, state: str, summary: str = "",
                      sources: list[dict[str, str]] | None = None, search_calls: int = 0,
                      facts: list[dict[str, Any]] | None = None,
                      gaps: str = "",
                      error_code: str | None = None, job_hash: str = "",
                      cache_scope: str = "job", researched_at: str = "") -> dict[str, Any]:
        if state not in {"READY", "UNAVAILABLE"} or len(summary) > 20_000 or len(gaps) > 5_000:
            raise JobStoreError("Risultato della ricerca aziendale non valido.")
        if cache_scope not in {"job", "company"}:
            raise JobStoreError("Ambito della cache di ricerca non valido.")
        safe_sources = []
        for source in (sources or [])[:30]:
            if not isinstance(source, dict):
                continue
            url, title = source.get("url", ""), source.get("title", "")
            if (isinstance(url, str) and url.startswith(("https://", "http://")) and
                    isinstance(title, str)):
                safe_sources.append({"url": url[:2_000], "title": title[:300]})
        allowed_urls = {source["url"] for source in safe_sources}
        safe_facts = []
        for fact in (facts or [])[:30]:
            if (not isinstance(fact, dict) or not isinstance(fact.get("claim"), str) or
                    fact.get("kind") not in {"documented_fact", "interpretation"}):
                continue
            urls = [url for url in fact.get("source_urls", [])
                    if isinstance(url, str) and url in allowed_urls][:10]
            if not urls:
                continue
            safe_facts.append({"claim": fact["claim"][:1_500], "kind": fact["kind"], "source_urls": urls})
        connection = self.store._connection()
        try:
            connection.execute(
                """INSERT INTO company_research
                   (linkedin_job_id,state,summary,job_hash,sources_json,facts_json,gaps,search_calls,updated_at,researched_at,error_code,cache_scope)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(linkedin_job_id) DO UPDATE SET
                   state=excluded.state, summary=excluded.summary, sources_json=excluded.sources_json,
                   facts_json=excluded.facts_json,gaps=excluded.gaps,
                   search_calls=excluded.search_calls, updated_at=excluded.updated_at,
                   researched_at=excluded.researched_at,error_code=excluded.error_code,
                   job_hash=excluded.job_hash,cache_scope=excluded.cache_scope""",
                (job_id, state, summary.strip(), job_hash[:64], json.dumps(safe_sources, ensure_ascii=False),
                 json.dumps(safe_facts, ensure_ascii=False), gaps.strip(),
                 max(0, min(int(search_calls), 100)), _now(), researched_at or _now(),
                 (error_code or "")[:80] or None, cache_scope),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get_research(job_id) or {}

    def get_company_research(self, company_name: str, *, mark_used: bool = True) -> dict[str, Any] | None:
        """Return the durable, source-backed company profile regardless of its age."""
        key = _company_cache_key(company_name)
        if not key:
            return None
        now = _now()
        connection = self.store._connection()
        try:
            if mark_used:
                connection.execute(
                    "UPDATE company_profiles SET last_used_at=?,use_count=use_count+1 WHERE company_key=?",
                    (now, key),
                )
            row = connection.execute(
                "SELECT * FROM company_profiles WHERE company_key=?",
                (key,),
            ).fetchone()
            if mark_used:
                connection.commit()
            if row is None:
                return None
            result = dict(row)
            result["updated_at"] = result["researched_at"]
            try:
                result["sources"] = json.loads(result.pop("sources_json"))
            except (ValueError, TypeError):
                result["sources"] = []
                result.pop("sources_json", None)
            try:
                result["facts"] = json.loads(result.pop("facts_json"))
            except (ValueError, TypeError):
                result["facts"] = []
                result.pop("facts_json", None)
            result["cache_scope"] = "company"
            return result
        finally:
            connection.close()

    def save_company_research(self, company_name: str, research: dict[str, Any]) -> bool:
        """Persist successful sourced company facts for reuse across applications."""
        if not isinstance(research, dict):
            return False
        researched_at = str(research.get("researched_at") or research.get("updated_at") or _now())
        connection = self.store._connection()
        try:
            saved = _upsert_company_profile(connection, company_name, research, researched_at)
            connection.commit()
            return saved
        finally:
            connection.close()

    def get_research(self, job_id: str) -> dict[str, Any] | None:
        connection = self.store._connection()
        try:
            row = connection.execute(
                "SELECT * FROM company_research WHERE linkedin_job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            if not result.get("researched_at"):
                result["researched_at"] = result.get("updated_at", "")
            try:
                result["sources"] = json.loads(result.pop("sources_json"))
            except (ValueError, TypeError):
                result["sources"] = []
                result.pop("sources_json", None)
            try:
                result["facts"] = json.loads(result.pop("facts_json"))
            except (ValueError, TypeError):
                result["facts"] = []
                result.pop("facts_json", None)
            return result
        finally:
            connection.close()

    def save_research_pages(self, job_id: str, research_hash: str,
                            pages: list[dict[str, Any]]) -> None:
        """Cache bounded text extracted from the exact URLs in the current research result."""
        research = self.get_research(job_id)
        if research is None:
            raise JobStoreError("Prima salva la ricerca aziendale associata alle fonti.")
        allowed_urls = {str(source.get("url") or "") for source in research.get("sources", [])
                        if isinstance(source, dict)}
        clean_pages = []
        seen_urls: set[str] = set()
        seen_sites: set[str] = set()
        for page in (pages or [])[:MAX_RESEARCH_PAGES * 4]:
            if not isinstance(page, dict):
                continue
            url = page.get("url")
            state = page.get("state")
            site_key = research_site_key(url) if isinstance(url, str) else ""
            if (not isinstance(url, str) or url not in allowed_urls or url in seen_urls or
                    not site_key or site_key in seen_sites or
                    state not in {"READY", "SKIPPED", "UNAVAILABLE"}):
                continue
            seen_urls.add(url)
            seen_sites.add(site_key)
            clean_page = {
                "url": url[:2_000], "title": str(page.get("title") or url)[:300],
                "state": state, "error_code": str(page.get("error_code") or "")[:80],
                "text": str(page.get("text") or "")[:6_000] if state == "READY" else "",
            }
            duration_ms = page.get("duration_ms")
            if type(duration_ms) is int and 0 <= duration_ms <= 120_000:
                clean_page["duration_ms"] = duration_ms
            fetched_at = page.get("fetched_at")
            if isinstance(fetched_at, str) and len(fetched_at) <= 40:
                clean_page["fetched_at"] = fetched_at
            clean_pages.append(clean_page)
            if len(clean_pages) >= MAX_RESEARCH_PAGES:
                break
        connection = self.store._connection()
        try:
            connection.execute(
                """INSERT INTO company_research_pages(linkedin_job_id,research_hash,pages_json,updated_at)
                   VALUES (?,?,?,?) ON CONFLICT(linkedin_job_id) DO UPDATE SET
                   research_hash=excluded.research_hash,pages_json=excluded.pages_json,updated_at=excluded.updated_at""",
                (job_id, str(research_hash)[:64], json.dumps(clean_pages, ensure_ascii=False), _now()),
            )
            connection.commit()
        finally:
            connection.close()

    def get_research_pages(self, job_id: str, research_hash: str) -> list[dict[str, str]] | None:
        connection = self.store._connection()
        try:
            row = connection.execute(
                "SELECT research_hash,pages_json FROM company_research_pages WHERE linkedin_job_id=?",
                (job_id,),
            ).fetchone()
            if row is None or row["research_hash"] != research_hash:
                return None
            pages = json.loads(row["pages_json"])
            return pages if isinstance(pages, list) else []
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        finally:
            connection.close()

    def add_chat_message(self, job_id: str, role: str, content: str,
                         proposal: dict[str, Any] | None = None) -> dict[str, Any]:
        if role not in {"user", "assistant"} or not isinstance(content, str) or not content.strip() or len(content) > MAX_CHAT_TEXT_CHARS:
            raise JobStoreError("Messaggio chat non valido o troppo lungo.")
        proposal_json = json.dumps(proposal, ensure_ascii=False) if proposal else None
        if proposal_json and len(proposal_json) > 130_000:
            raise JobStoreError("La proposta della chat è troppo grande.")
        connection = self.store._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute("SELECT 1 FROM jobs WHERE linkedin_job_id = ?", (job_id,)).fetchone():
                raise JobStoreError("Annuncio non trovato.")
            connection.execute(
                "INSERT INTO job_chat_messages(linkedin_job_id,role,content,proposal_json,created_at) VALUES (?,?,?,?,?)",
                (job_id, role, content.strip(), proposal_json, _now()),
            )
            connection.execute(
                """DELETE FROM job_chat_messages WHERE linkedin_job_id = ? AND id NOT IN
                   (SELECT id FROM job_chat_messages WHERE linkedin_job_id = ? ORDER BY id DESC LIMIT ?)""",
                (job_id, job_id, MAX_CHAT_MESSAGES_PER_JOB),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.chat_history(job_id)

    def chat_history(self, job_id: str, limit: int = 100) -> list[dict[str, Any]]:
        connection = self.store._connection()
        try:
            rows = connection.execute(
                """SELECT role,content,proposal_json,created_at FROM job_chat_messages
                   WHERE linkedin_job_id = ? ORDER BY id DESC LIMIT ?""",
                (job_id, max(1, min(limit, MAX_CHAT_MESSAGES_PER_JOB))),
            ).fetchall()
            result = []
            for row in reversed(rows):
                item = dict(row)
                raw = item.pop("proposal_json")
                if raw:
                    try:
                        item["proposal"] = json.loads(raw)
                    except ValueError:
                        item["proposal"] = None
                else:
                    item["proposal"] = None
                result.append(item)
            return result
        finally:
            connection.close()

    def add_knowledge(self, filename: str, file_format: str, contents: bytes, extracted_text: str) -> dict[str, Any]:
        normalized = file_format.casefold().lstrip(".")
        clean_name = filename.replace("\\", "/").split("/")[-1].strip()
        if normalized not in {"pdf", "docx", "txt", "md"} or not clean_name or len(clean_name) > 240:
            raise JobStoreError("Formato o nome del file di esempio non valido.")
        if not contents or len(contents) > MAX_KNOWLEDGE_FILE_BYTES:
            raise JobStoreError("Il file di esempio è vuoto o supera 8 MB.")
        if not extracted_text.strip() or len(extracted_text) > MAX_KNOWLEDGE_TEXT_CHARS:
            raise JobStoreError("Il testo estratto dall'esempio non è valido o è troppo lungo.")
        connection = self.store._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            count = int(connection.execute("SELECT COUNT(*) AS n FROM cover_letter_knowledge").fetchone()["n"])
            if count >= MAX_KNOWLEDGE_FILES:
                raise JobStoreError("Cover Letter knowledge contiene già dieci esempi. Rimuovine uno prima di aggiungerne un altro.")
            cursor = connection.execute(
                """INSERT INTO cover_letter_knowledge(filename,file_format,content,extracted_text,created_at)
                   VALUES (?,?,?,?,?)""",
                (clean_name, normalized, sqlite3.Binary(contents), extracted_text.strip(), _now()),
            )
            row_id = int(cursor.lastrowid)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.knowledge_file(row_id, include_text=False) or {}

    def replace_knowledge(self, file_id: int, filename: str, file_format: str,
                          contents: bytes, extracted_text: str) -> dict[str, Any] | None:
        normalized = file_format.casefold().lstrip(".")
        clean_name = filename.replace("\\", "/").split("/")[-1].strip()
        if normalized not in {"pdf", "docx", "txt", "md"} or not clean_name or len(clean_name) > 240:
            raise JobStoreError("Formato o nome del file di esempio non valido.")
        if not contents or len(contents) > MAX_KNOWLEDGE_FILE_BYTES:
            raise JobStoreError("Il file di esempio è vuoto o supera 8 MB.")
        if not extracted_text.strip() or len(extracted_text) > MAX_KNOWLEDGE_TEXT_CHARS:
            raise JobStoreError("Il testo estratto dall'esempio non è valido o è troppo lungo.")
        connection = self.store._connection()
        try:
            cursor = connection.execute(
                """UPDATE cover_letter_knowledge SET filename=?,file_format=?,content=?,extracted_text=?,created_at=?
                   WHERE id=?""",
                (clean_name, normalized, sqlite3.Binary(contents), extracted_text.strip(),
                 datetime.now(timezone.utc).isoformat(timespec="microseconds"), file_id),
            )
            connection.commit()
            return self.knowledge_file(file_id, include_text=False) if cursor.rowcount else None
        finally:
            connection.close()

    def list_knowledge(self) -> list[dict[str, Any]]:
        connection = self.store._connection()
        try:
            rows = connection.execute(
                "SELECT id,filename,file_format,length(content) AS size_bytes,created_at FROM cover_letter_knowledge ORDER BY id"
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def knowledge_file(self, file_id: int, *, include_text: bool = True) -> dict[str, Any] | None:
        connection = self.store._connection()
        try:
            fields = "id,filename,file_format,length(content) AS size_bytes,created_at,content,extracted_text"
            row = connection.execute(f"SELECT {fields} FROM cover_letter_knowledge WHERE id = ?", (file_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            if not include_text:
                result.pop("content", None)
                result.pop("extracted_text", None)
            return result
        finally:
            connection.close()

    def knowledge_text(self) -> list[dict[str, str]]:
        connection = self.store._connection()
        try:
            rows = connection.execute(
                "SELECT filename,extracted_text FROM cover_letter_knowledge "
                "ORDER BY created_at DESC,id DESC LIMIT ?",
                (MAX_KNOWLEDGE_FILES,),
            ).fetchall()
            return [{"filename": str(row["filename"]), "text": str(row["extracted_text"])} for row in rows]
        finally:
            connection.close()

    def relevant_knowledge(self, job: dict[str, Any], limit: int = 3) -> list[dict[str, str]]:
        """Choose a few local style examples with overlapping role/company terms."""
        import re

        examples = self.knowledge_text()
        limit = max(0, min(int(limit), 3))
        if limit == 0:
            return []

        def bounded(selected: list[dict[str, str]]) -> list[dict[str, str]]:
            result = []
            for item in selected[:limit]:
                text = item["text"].strip()
                if len(text) > 2_800:
                    head, tail = text[:2_150], text[-500:]
                    boundary = max(head.rfind(". "), head.rfind("\n"))
                    if boundary > 1_500:
                        head = head[:boundary + 1]
                    text = head.rstrip() + "\n[… esempio abbreviato …]\n" + tail.lstrip()
                result.append({"filename": item["filename"], "text": text})
            return result

        if len(examples) <= limit:
            return bounded(examples)
        stop_words = {"about", "after", "alla", "della", "delle", "dello", "from", "into",
                      "nella", "nelle", "per", "role", "that", "the", "this", "with"}
        role_terms = {
            word.casefold() for word in re.findall(r"[\wÀ-ÿ]{3,}", " ".join(
                str(job.get(field) or "") for field in ("title", "company", "location", "description")))
            if word.casefold() not in stop_words
        }
        ranked = []
        for index, example in enumerate(examples):
            example_terms = {word.casefold() for word in re.findall(r"[\wÀ-ÿ]{3,}", example["text"])}
            ranked.append((len(role_terms & example_terms), -index, example))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        if ranked and ranked[0][0] > 0:
            return bounded([entry[2] for entry in ranked[:limit]])
        return bounded(examples[:limit])

    def delete_knowledge(self, file_id: int) -> bool:
        connection = self.store._connection()
        try:
            cursor = connection.execute("DELETE FROM cover_letter_knowledge WHERE id = ?", (file_id,))
            connection.commit()
            return cursor.rowcount > 0
        finally:
            connection.close()

    def add_feedback(self, job_id: str, version: int, rating: int, comment: str, learning_rule: str = "") -> None:
        if type(rating) is not int or not 1 <= rating <= 10 or not isinstance(comment, str) or len(comment) > 240:
            raise JobStoreError("Voto o commento di feedback non valido.")
        connection = self.store._connection()
        try:
            connection.execute(
                """INSERT INTO cover_letter_feedback
                   (linkedin_job_id,version,rating,comment,created_at,learning_rule) VALUES (?,?,?,?,?,?)""",
                (job_id, version, rating, comment.strip(), _now(), learning_rule[:800]),
            )
            connection.commit()
        finally:
            connection.close()

    def feedback_history(self, job_id: str, limit: int = 40) -> list[dict[str, Any]]:
        connection = self.store._connection()
        try:
            rows = connection.execute(
                """SELECT version,rating,comment,created_at,learning_rule FROM cover_letter_feedback
                   WHERE linkedin_job_id = ? ORDER BY id DESC LIMIT ?""", (job_id, max(1, min(limit, 100)))
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def record_learning(self, agent_id: str, agent_name: str, rule: str, backup_path: str) -> None:
        connection = self.store._connection()
        try:
            connection.execute(
                "INSERT INTO agent_learning(agent_id,agent_name,rule,backup_path,created_at) VALUES (?,?,?,?,?)",
                (agent_id[:48], agent_name[:80], rule[:800], backup_path[:500], _now()),
            )
            connection.commit()
        finally:
            connection.close()

    def learning_history(self, limit: int = 30) -> list[dict[str, Any]]:
        connection = self.store._connection()
        try:
            rows = connection.execute(
                "SELECT id,agent_id,agent_name,rule,backup_path,created_at FROM agent_learning ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def get_learning(self, learning_id: int) -> dict[str, Any] | None:
        connection = self.store._connection()
        try:
            row = connection.execute(
                "SELECT id,agent_id,agent_name,rule FROM agent_learning WHERE id = ?", (learning_id,)
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            connection.close()

    def delete_learning(self, learning_id: int) -> bool:
        connection = self.store._connection()
        try:
            cursor = connection.execute("DELETE FROM agent_learning WHERE id = ?", (learning_id,))
            connection.commit()
            return cursor.rowcount > 0
        finally:
            connection.close()

    def daily_call_count(self) -> int:
        today = datetime.now(timezone.utc).date()
        start = f"{today.isoformat()}T00:00:00"
        end = f"{(today + timedelta(days=1)).isoformat()}T00:00:00"
        connection = self.store._connection()
        try:
            return int(connection.execute(
                "SELECT (SELECT COUNT(*) FROM ai_usage WHERE created_at>=? AND created_at<?) + "
                "(SELECT COUNT(*) FROM api_operation_usage WHERE created_at>=? AND created_at<?) AS n",
                (start, end, start, end)
            ).fetchone()["n"])
        finally:
            connection.close()
