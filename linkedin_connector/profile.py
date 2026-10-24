"""Local Markdown instructions and candidate CV storage for the dashboard."""

from __future__ import annotations

import os
import hashlib
import json
import re
import secrets
import shutil
import threading
import zipfile
from datetime import datetime, timezone
from functools import wraps
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from linkedin_connector.ai_config import MODELS

MAX_AGENT_MARKDOWN_CHARS = 100_000
MAX_CV_TEXT_CHARS = 50_000
SUPPORTED_CV_FORMATS = {"pdf", "docx", "txt", "md"}
_CV_FILENAME = re.compile(r"^cv\.(pdf|docx|txt|md)$")
_PDF_EXTRACTOR_AVAILABLE = None


class LocalProfileError(ValueError):
    """A local profile or agent-instructions operation failed validation."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _profile_locked(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._guard:
            return method(self, *args, **kwargs)
    return locked


class AgentPromptStore:
    """Keep one per-installation Markdown file, seeding it from a neutral template."""

    def __init__(
        self,
        path: Path,
        default_path: Path | None = None,
        *,
        label: str = "file Markdown degli agenti",
    ) -> None:
        self.path = path.resolve()
        self.label = label
        self._guard = threading.RLock()
        if not self.path.exists() and default_path is not None:
            try:
                default_text = default_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as error:
                raise LocalProfileError(f"Non riesco a leggere il modello per il {self.label}.", 500) from error
            self.save(default_text)

    @_profile_locked
    def load(self) -> str:
        try:
            instructions = self.path.read_text(encoding="utf-8")
        except OSError as error:
            raise LocalProfileError(f"Non riesco a leggere il {self.label}.", 500) from error
        except UnicodeDecodeError as error:
            raise LocalProfileError(f"Il {self.label} non è codificato in UTF-8.", 500) from error
        if len(instructions) > MAX_AGENT_MARKDOWN_CHARS:
            raise LocalProfileError(f"Il {self.label} supera il limite di 100.000 caratteri.", 413)
        return instructions

    @_profile_locked
    def save(self, instructions: object) -> str:
        if not isinstance(instructions, str):
            raise LocalProfileError(f"Il {self.label} deve essere testo.")
        if not instructions.strip():
            raise LocalProfileError(f"Il {self.label} non può essere vuoto.")
        if len(instructions) > MAX_AGENT_MARKDOWN_CHARS:
            raise LocalProfileError(f"Il {self.label} supera il limite di 100.000 caratteri.", 413)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f".{self.path.name}-{secrets.token_hex(8)}.tmp")
        try:
            with temp_path.open("x", encoding="utf-8", newline="\n") as output:
                output.write(instructions.rstrip() + "\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_path, self.path)
        except OSError as error:
            temp_path.unlink(missing_ok=True)
            raise LocalProfileError(f"Impossibile salvare il {self.label}.", 500) from error
        return instructions.rstrip() + "\n"

    def agents(self) -> list[dict[str, Any]]:
        """Parse the configured workflow roles directly from this Markdown file."""
        return parse_agent_markdown(self.load())

    @_profile_locked
    def general_instructions(self) -> str:
        """Return shared rules without resending every agent definition on each call."""
        instructions = self.load()
        start, end, _, _ = _agent_region(instructions)
        if start is None or end is None:
            return instructions
        return (instructions[:start] + instructions[end:]).strip()

    @_profile_locked
    def replace_agents(self, agents: object) -> str:
        """Update only the workflow agent blocks, retaining every other Markdown section."""
        current = self.load()
        normalized = validate_agent_list(agents)
        start, end, prefix, blocks = _agent_region(current)
        if start is None:
            if current and not current.endswith("\n"):
                current += "\n"
            current += "\n## Internal roles and workflow\n\n"
            start, end, prefix, blocks = _agent_region(current)
        preamble = prefix
        if preamble.strip():
            preamble = preamble.rstrip() + "\n\n"
        rendered: list[str] = []
        for index, agent in enumerate(normalized, start=1):
            model = f" model={agent['model']}" if agent.get("model") else ""
            metadata = (f"<!-- agent:id={agent['id']} phase={_metadata_value(agent['phase'])} "
                        f"enabled={'true' if agent['enabled'] else 'false'}{model} -->")
            heading = f"### {index}. {agent['name']}"
            body = agent["instructions"].strip()
            rendered.append(f"{heading}\n{metadata}\n\n{body}".rstrip())
        replacement = preamble + "\n\n".join(rendered) + "\n"
        updated = current[:start] + replacement + current[end:]
        return self.save(updated)

    @_profile_locked
    def append_learning_rule(self, agent_id: str, rule: str, *, forbidden: tuple[str, ...] = ()) -> dict[str, str]:
        rule = " ".join(rule.split()).strip(" -•")
        if not rule or len(rule) > 500 or any(token and token.casefold() in rule.casefold() for token in forbidden):
            raise LocalProfileError("La regola proposta non è abbastanza generale per gli agenti futuri.")
        if re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b\+?\d[\d ()-]{7,}\d\b|https?://", rule):
            raise LocalProfileError("La regola proposta contiene dati specifici e non viene aggiunta agli agenti.")
        agents = self.agents()
        selected = next((agent for agent in agents if agent["id"] == agent_id), None)
        if selected is None:
            raise LocalProfileError("Agente di destinazione non trovato.")
        marker = hashlib.sha256((agent_id + "\0" + rule.casefold()).encode("utf-8")).hexdigest()[:16]
        if marker in selected["instructions"]:
            return {"agent_id": agent_id, "agent_name": selected["name"], "rule": rule, "backup_path": ""}
        backup_path = self._backup_path("learning")
        try:
            shutil.copy2(self.path, backup_path)
        except OSError as error:
            raise LocalProfileError("Non riesco a creare una copia dell'agente prima della modifica.", 500) from error
        body = selected["instructions"].rstrip()
        if "#### Regole apprese dal feedback" not in body:
            body += "\n\n#### Regole apprese dal feedback"
        body += f"\n\n<!-- feedback-rule:{marker} -->\n- {rule}"
        selected["instructions"] = body
        try:
            self.replace_agents(agents)
        except Exception:
            backup_path.unlink(missing_ok=True)
            raise
        return {"agent_id": agent_id, "agent_name": selected["name"], "rule": rule,
                "backup_path": str(backup_path)}

    @_profile_locked
    def remove_learning_rule(self, agent_id: str, rule: str) -> None:
        marker = hashlib.sha256((agent_id + "\0" + " ".join(rule.split()).strip(" -•").casefold()).encode("utf-8")).hexdigest()[:16]
        agents = self.agents()
        selected = next((agent for agent in agents if agent["id"] == agent_id), None)
        if selected is None:
            raise LocalProfileError("Agente di destinazione non trovato.")
        pattern = re.compile(rf"\n*<!-- feedback-rule:{re.escape(marker)} -->\n- [^\n]+")
        updated, count = pattern.subn("", selected["instructions"])
        if count:
            updated = re.sub(r"\n*#### Regole apprese dal feedback\s*$", "", updated).rstrip()
            selected["instructions"] = updated or "Istruzioni da completare."
            self.replace_agents(agents)

    def _backup_path(self, suffix: str) -> Path:
        directory = self.path.parent / "agent-history"
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        return directory / f"AGENTS.{suffix}-{stamp}.md"


_AGENT_PARENT = re.compile(r"^##\s+(.+?)\s*$", re.IGNORECASE)
_AGENT_HEADING = re.compile(r"^###\s+(.+?)\s*$")
_AGENT_METADATA = re.compile(
    r"^<!--\s*agent:id=([a-z0-9_-]{1,48})\s+phase=([a-zA-Z0-9_-]{1,48})\s+enabled=(true|false)"
    r"(?:\s+model=([a-zA-Z0-9._-]{1,48}))?\s*-->$"
)
_AGENT_ID = re.compile(r"[^a-z0-9_-]+")


def _metadata_value(value: str) -> str:
    normalized = _AGENT_ID.sub("-", value.casefold()).strip("-_")
    return normalized[:48] or "fase"


def _agent_region(markdown: str) -> tuple[int | None, int | None, str, list[tuple[int, int, str, str, bool, str, str, str | None]]]:
    lines = markdown.splitlines(keepends=True)
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line)
    parent_index = None
    for index, line in enumerate(lines):
        match = _AGENT_PARENT.match(line.rstrip("\r\n"))
        if match and match.group(1).strip().casefold() in {
            "internal roles and workflow", "workflow agenti", "agenti", "agents",
        }:
            parent_index = index
            break
    if parent_index is None:
        return None, None, "", []
    region_end_index = len(lines)
    for index in range(parent_index + 1, len(lines)):
        if re.match(r"^##\s+", lines[index]):
            region_end_index = index
            break
    region_start_index = parent_index + 1
    heading_indices = [index for index in range(region_start_index, region_end_index)
                       if _AGENT_HEADING.match(lines[index].rstrip("\r\n"))]
    if not heading_indices:
        region_start = offsets[region_start_index] if region_start_index < len(offsets) else len(markdown)
        region_end = offsets[region_end_index] if region_end_index < len(offsets) else len(markdown)
        return region_start, region_end, "", []
    blocks = []
    for position, heading_index in enumerate(heading_indices):
        block_end_index = heading_indices[position + 1] if position + 1 < len(heading_indices) else region_end_index
        heading = _AGENT_HEADING.match(lines[heading_index].rstrip("\r\n"))
        assert heading is not None
        title = re.sub(r"^\d+\.\s*", "", heading.group(1)).strip()
        content_start = heading_index + 1
        agent_id = _metadata_value(title)
        phase = _metadata_value(title)
        enabled = True
        model = None
        if content_start < block_end_index:
            metadata = _AGENT_METADATA.match(lines[content_start].strip())
            if metadata:
                agent_id, phase, enabled_text, model = metadata.groups()
                enabled = enabled_text == "true"
                if model not in MODELS and model is not None:
                    raise LocalProfileError(f"Il modello configurato per l’agente '{title}' non è supportato.")
                content_start += 1
                if content_start < block_end_index and not lines[content_start].strip():
                    content_start += 1
        body = "".join(lines[content_start:block_end_index]).strip()
        blocks.append((offsets[heading_index],
                       offsets[block_end_index] if block_end_index < len(offsets) else len(markdown),
                       agent_id, title, enabled, phase, body, model))
    first_heading = heading_indices[0]
    prefix = "".join(lines[region_start_index:first_heading])
    region_start = offsets[region_start_index] if region_start_index < len(offsets) else len(markdown)
    region_end = offsets[region_end_index] if region_end_index < len(offsets) else len(markdown)
    return region_start, region_end, prefix, blocks


def parse_agent_markdown(markdown: str) -> list[dict[str, Any]]:
    if not isinstance(markdown, str) or not markdown.strip():
        raise LocalProfileError("Il file AGENTS.md è vuoto.")
    _, _, _, blocks = _agent_region(markdown)
    if not blocks:
        raise LocalProfileError("AGENTS.md non contiene agenti riconoscibili sotto 'Internal roles and workflow'.")
    agents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, _, agent_id, title, enabled, phase, body, model in blocks:
        if agent_id in seen or not title or len(title) > 80 or not body or len(body) > 30_000:
            raise LocalProfileError("Una sezione agente di AGENTS.md è ambigua o non valida.")
        seen.add(agent_id)
        agents.append({"id": agent_id, "name": title, "phase": phase.replace("-", " ").title(),
                       "enabled": enabled, "instructions": body,
                       "summary": _summarize_agent(body), "model": model})
    if not any(agent["enabled"] for agent in agents):
        raise LocalProfileError("Attiva almeno un agente in AGENTS.md.")
    return agents


def validate_agent_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 12:
        raise LocalProfileError("Configura da 1 a 12 agenti nel file AGENTS.md.")
    result = []
    seen: set[str] = set()
    for agent in value:
        if not isinstance(agent, dict) or set(agent) - {
                "id", "name", "phase", "enabled", "instructions", "summary", "model"}:
            raise LocalProfileError("Un agente contiene campi non validi.")
        agent_id = agent.get("id")
        if not isinstance(agent_id, str) or not re.fullmatch(r"[a-z0-9_-]{1,48}", agent_id) or agent_id in seen:
            raise LocalProfileError("ID agente duplicato o non valido.")
        seen.add(agent_id)
        normalized = {"id": agent_id}
        for field, limit in (("name", 80), ("phase", 48), ("instructions", 30_000)):
            text = agent.get(field)
            if not isinstance(text, str) or not text.strip() or len(text) > limit:
                raise LocalProfileError(f"Il campo {field} di un agente non è valido.")
            normalized[field] = text.strip()
        if type(agent.get("enabled")) is not bool:
            raise LocalProfileError("Lo stato attivo di un agente non è valido.")
        normalized["enabled"] = agent["enabled"]
        model = agent.get("model")
        if model in (None, ""):
            normalized["model"] = None
        elif not isinstance(model, str) or model not in MODELS:
            raise LocalProfileError("Il modello configurato per un agente non è supportato.")
        else:
            normalized["model"] = model
        result.append(normalized)
    if not any(agent["enabled"] for agent in result):
        raise LocalProfileError("Attiva almeno un agente.")
    return result


def _summarize_agent(body: str) -> str:
    lines = [re.sub(r"^[\s>*-]+", "", line).strip() for line in body.splitlines()]
    lines = [re.sub(r"[`*_]", "", line) for line in lines if line.strip() and not line.lstrip().startswith("<!--")]
    summary = " ".join(lines[:2])
    return summary if len(summary) <= 280 else summary[:279] + "…"


def pdf_extraction_available() -> bool:
    global _PDF_EXTRACTOR_AVAILABLE
    if _PDF_EXTRACTOR_AVAILABLE is None:
        try:
            import pypdf  # noqa: F401
        except ImportError:
            _PDF_EXTRACTOR_AVAILABLE = False
        else:
            _PDF_EXTRACTOR_AVAILABLE = True
    return bool(_PDF_EXTRACTOR_AVAILABLE)


class CandidateProfileStore:
    """Store exactly one active CV under the local, ignored application data folder."""

    def __init__(self, data_directory: Path) -> None:
        self.directory = data_directory.resolve() / "candidate_profile"
        self.metadata_path = self.directory / "cv-metadata.json"

    def _original_filename(self, current: Path) -> tuple[str, bool]:
        try:
            metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            raw_name = metadata.get("filename") if isinstance(metadata, dict) else None
            name = str(raw_name or "").replace("\\", "/").split("/")[-1].strip()
            if (name and len(name) <= 255 and
                    Path(name).suffix.casefold() == current.suffix.casefold()):
                return name, True
        except (OSError, UnicodeDecodeError, ValueError, TypeError):
            pass
        return current.name, False

    def status(self) -> dict[str, object]:
        current = self._current_file()
        if current is None:
            return {
                "has_cv": False,
                "filename": None,
                "original_filename_available": False,
                "format": None,
                "size_bytes": 0,
                "modified_at": None,
                "pdf_extraction_available": pdf_extraction_available(),
            }
        stat = current.stat()
        filename, original_filename_available = self._original_filename(current)
        return {
            "has_cv": True,
            "filename": filename,
            "original_filename_available": original_filename_available,
            "format": current.suffix.lstrip(".").lower(),
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "pdf_extraction_available": pdf_extraction_available(),
        }

    def save(self, filename: str, file_format: str, contents: bytes) -> dict[str, object]:
        normalized_format = file_format.casefold().lstrip(".")
        filename = filename.replace("\\", "/").split("/")[-1].strip()
        if normalized_format not in SUPPORTED_CV_FORMATS:
            raise LocalProfileError("Sono consentiti CV PDF, DOCX, TXT o MD.")
        if not filename or len(filename) > 255:
            raise LocalProfileError("Nome del file CV non valido.")
        if not contents:
            raise LocalProfileError("Il file CV è vuoto.")
        # Validate extraction before replacing any previously stored CV.
        extract_cv_text(normalized_format, contents)
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"cv.{normalized_format}"
        temp_path = self.directory / f".cv-{secrets.token_hex(8)}.tmp"
        metadata_temp = self.directory / f".cv-metadata-{secrets.token_hex(8)}.tmp"
        cv_replaced = False
        try:
            with temp_path.open("xb") as output:
                output.write(contents)
                output.flush()
                os.fsync(output.fileno())
            with metadata_temp.open("x", encoding="utf-8") as output:
                json.dump({"filename": filename}, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_path, target)
            cv_replaced = True
            os.replace(metadata_temp, self.metadata_path)
            for candidate in self.directory.iterdir():
                if candidate != target and _CV_FILENAME.fullmatch(candidate.name):
                    candidate.unlink(missing_ok=True)
        except OSError as error:
            temp_path.unlink(missing_ok=True)
            metadata_temp.unlink(missing_ok=True)
            if cv_replaced:
                self.metadata_path.unlink(missing_ok=True)
            raise LocalProfileError("Impossibile salvare il CV nella cartella locale.", 500) from error
        return self.status()

    def read_text(self) -> str:
        current = self._current_file()
        if current is None:
            raise LocalProfileError("Carica prima il tuo CV in Impostazioni.", 409)
        try:
            contents = current.read_bytes()
        except OSError as error:
            raise LocalProfileError("Non riesco a leggere il CV salvato sul PC.", 500) from error
        return extract_cv_text(current.suffix.lstrip(".").lower(), contents)

    def delete(self) -> None:
        if not self.directory.exists():
            return
        try:
            for candidate in self.directory.iterdir():
                if (_CV_FILENAME.fullmatch(candidate.name) or candidate.name.startswith(".cv-") or
                        candidate == self.metadata_path):
                    candidate.unlink(missing_ok=True)
        except OSError as error:
            raise LocalProfileError("Impossibile rimuovere il CV dalla cartella locale.", 500) from error

    def _current_file(self) -> Path | None:
        if not self.directory.exists():
            return None
        try:
            candidates = [
                path for path in self.directory.iterdir()
                if path.is_file() and _CV_FILENAME.fullmatch(path.name)
            ]
            return max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else None
        except OSError as error:
            raise LocalProfileError("Non riesco a consultare il CV locale.", 500) from error


def extract_cv_text(file_format: str, contents: bytes) -> str:
    """Extract bounded plain text without changing or uploading the source file."""
    normalized_format = file_format.casefold().lstrip(".")
    try:
        if normalized_format in {"txt", "md"}:
            text = contents.decode("utf-8-sig")
        elif normalized_format == "docx":
            text = _extract_docx_text(contents)
        elif normalized_format == "pdf":
            text = _extract_pdf_text(contents)
        else:
            raise LocalProfileError("Formato CV non supportato.")
    except UnicodeDecodeError as error:
        raise LocalProfileError("Il CV di testo deve essere codificato in UTF-8.") from error
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32).strip()
    if not text:
        raise LocalProfileError("Non ho trovato testo leggibile nel CV.")
    if len(text) > MAX_CV_TEXT_CHARS:
        raise LocalProfileError("Il testo estratto supera 50.000 caratteri; usa una versione più breve del CV.", 413)
    return text


def _extract_docx_text(contents: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(contents)) as archive:
            entries = archive.infolist()
            document = archive.getinfo("word/document.xml")
            if len(entries) > 2_000 or sum(entry.file_size for entry in entries) > 20 * 1024 * 1024:
                raise LocalProfileError("Il DOCX supera il limite di dimensione estratta.", 413)
            if document.file_size > 10 * 1024 * 1024:
                raise LocalProfileError("Il contenuto Word supera il limite di estrazione.", 413)
            root = ElementTree.fromstring(archive.read(document))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as error:
        raise LocalProfileError("Il contenuto non è un file DOCX valido.") from error
    paragraphs: list[str] = []
    for paragraph in root.iter():
        if paragraph.tag.rsplit("}", 1)[-1] != "p":
            continue
        pieces: list[str] = []
        for node in paragraph.iter():
            kind = node.tag.rsplit("}", 1)[-1]
            if kind == "t" and node.text:
                pieces.append(node.text)
            elif kind == "tab":
                pieces.append("\t")
            elif kind in {"br", "cr"}:
                pieces.append("\n")
        if pieces:
            paragraphs.append("".join(pieces))
    return "\n".join(paragraphs)


def _extract_pdf_text(contents: bytes) -> str:
    if not contents[:1024].lstrip().startswith(b"%PDF-"):
        raise LocalProfileError("Il contenuto non sembra essere un file PDF valido.")
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise LocalProfileError(
            "Per leggere CV PDF installa le dipendenze aggiornate del progetto (pypdf).", 501
        ) from error
    try:
        reader: Any = PdfReader(BytesIO(contents), strict=True)
        if reader.is_encrypted:
            raise LocalProfileError("Il CV PDF è protetto da password; carica una copia non protetta.")
        if len(reader.pages) > 100:
            raise LocalProfileError("Il CV PDF supera il limite di 100 pagine.", 413)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except LocalProfileError:
        raise
    except Exception as error:
        raise LocalProfileError("Non riesco a estrarre testo dal PDF. Verifica che non sia una scansione immagine.") from error
