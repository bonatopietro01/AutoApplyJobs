"""Per-installation AI settings and operating-system-backed API credentials."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODELS = ("gpt-5.6-luna", "gpt-4.1-nano", "gpt-4o-mini", "gpt-4.1-mini", "gpt-5-mini")
ENV_API_KEY = "LINKEDIN_CONNECTOR_OPENAI_API_KEY"
SETTINGS_FIELDS = {
    "provider", "model", "enabled", "consent", "max_per_cycle",
    "web_research_enabled", "max_daily_calls", "max_daily_agent_calls",
    "max_daily_research_calls", "max_daily_revision_calls",
    "max_auto_job_age_days",
}


class AIConfigurationError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def default_settings() -> dict[str, Any]:
    return {"provider": "openai", "model": "gpt-5.6-luna", "enabled": False,
            "consent": False, "max_per_cycle": 5, "web_research_enabled": True,
            "max_daily_calls": 200, "max_daily_agent_calls": 200,
            "max_daily_research_calls": 20,
            "max_auto_job_age_days": 90,
            "max_daily_revision_calls": 20}


def validate_settings(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != SETTINGS_FIELDS:
        raise AIConfigurationError("Configurazione AI non valida.")
    if value["provider"] != "openai" or value["model"] not in MODELS:
        raise AIConfigurationError("Provider o modello non supportato.")
    if (type(value["enabled"]) is not bool or type(value["consent"]) is not bool or
            type(value["web_research_enabled"]) is not bool):
        raise AIConfigurationError("Attivazione o consenso non valido.")
    if type(value["max_per_cycle"]) is not int or not 1 <= value["max_per_cycle"] <= 20:
        raise AIConfigurationError("Il limite per ciclo deve essere compreso tra 1 e 20.")
    if type(value["max_daily_calls"]) is not int or not 1 <= value["max_daily_calls"] <= 2_000:
        raise AIConfigurationError("Il limite giornaliero deve essere compreso tra 1 e 2.000 chiamate.")
    for field in ("max_daily_agent_calls", "max_daily_research_calls", "max_daily_revision_calls"):
        if type(value[field]) is not int or not 0 <= value[field] <= 2_000:
            raise AIConfigurationError("I limiti giornalieri per tipo devono essere compresi tra 0 e 2.000.")
    if type(value["max_auto_job_age_days"]) is not int or not 1 <= value["max_auto_job_age_days"] <= 3_650:
        raise AIConfigurationError("L’età massima degli annunci deve essere compresa tra 1 e 3.650 giorni.")
    return dict(value)


class AISettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return default_settings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("not an object")
            if self.migration_pending(raw):
                legacy = {key: raw[key] for key in SETTINGS_FIELDS if key in raw}
                defaults = default_settings()
                for field, default in defaults.items():
                    legacy.setdefault(field, default)
                return validate_settings(legacy)
            raw = {key: value for key, value in raw.items()
                   if key not in {"schema_version", "max_daily_chat_calls"}}
            for field, default in default_settings().items():
                raw.setdefault(field, default)
            return validate_settings(raw)
        except (OSError, ValueError, UnicodeDecodeError) as error:
            raise AIConfigurationError("Non riesco a leggere le impostazioni AI locali.", 500) from error

    @staticmethod
    def migration_pending(raw: object) -> bool:
        return isinstance(raw, dict) and "agents" in raw and raw.get("schema_version") != 2

    def legacy_agents(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as error:
            raise AIConfigurationError("Non riesco a leggere la vecchia configurazione agenti.", 500) from error
        agents = raw.get("agents") if isinstance(raw, dict) else None
        if not isinstance(agents, list):
            return []
        from linkedin_connector.profile import validate_agent_list
        try:
            return validate_agent_list(agents)
        except ValueError as error:
            raise AIConfigurationError("La vecchia configurazione agenti richiede un recupero manuale.", 409) from error

    def save(self, value: object, *, resolve_legacy: bool = False) -> dict[str, Any]:
        settings = validate_settings(value)
        if self.migration_pending_on_disk() and not resolve_legacy:
            raise AIConfigurationError("Scegli prima come migrare gli agenti in AGENTS.md.", 409)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(f".{self.path.name}-{secrets.token_hex(8)}.tmp")
        try:
            with temp.open("x", encoding="utf-8") as output:
                json.dump({**settings, "schema_version": 2}, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, self.path)
        except OSError as error:
            temp.unlink(missing_ok=True)
            raise AIConfigurationError("Impossibile salvare le impostazioni AI.", 500) from error
        return settings

    def migration_pending_on_disk(self) -> bool:
        if not self.path.exists():
            return False
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as error:
            raise AIConfigurationError("Non riesco a leggere le impostazioni AI locali.", 500) from error
        return self.migration_pending(raw)

    def finish_agent_migration(self, choice: str, agent_prompt: Any) -> dict[str, str]:
        if choice not in {"keep_markdown", "use_saved_agents"}:
            raise AIConfigurationError("Scelta di migrazione non valida.")
        if not self.migration_pending_on_disk():
            raise AIConfigurationError("La migrazione degli agenti è già stata completata.", 409)
        agents = self.legacy_agents() if choice == "use_saved_agents" else []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_settings = self.path.with_name(f"automation.pre-agent-migration-{stamp}.json")
        backup_agents = agent_prompt.path.with_name(f"AGENTS.pre-agent-migration-{stamp}.md")
        try:
            shutil.copy2(self.path, backup_settings)
            shutil.copy2(agent_prompt.path, backup_agents)
        except OSError as error:
            backup_settings.unlink(missing_ok=True)
            backup_agents.unlink(missing_ok=True)
            raise AIConfigurationError("Non riesco a creare le copie di sicurezza per la migrazione.", 500) from error
        if choice == "use_saved_agents":
            try:
                agent_prompt.replace_agents(agents)
            except Exception:
                raise
        raw = json.loads(backup_settings.read_text(encoding="utf-8"))
        operational = {key: raw[key] for key in SETTINGS_FIELDS if key in raw}
        defaults = default_settings()
        for field, value in defaults.items():
            operational.setdefault(field, value)
        saved = self.save(operational, resolve_legacy=True)
        return {"settings_backup": str(backup_settings), "agents_backup": str(backup_agents),
                "choice": choice}


class APIKeyStore:
    """Store a key in the OS credential backend; support an environment-only fallback."""

    def __init__(self, database_path: Path, backend: Any | None = None) -> None:
        self.service = "LinkedIn Job Connector OpenAI"
        self.username = hashlib.sha256(str(database_path.resolve()).encode("utf-8")).hexdigest()
        self._backend = backend

    def _keyring(self) -> Any:
        if self._backend is not None:
            return self._backend
        try:
            import keyring
            backend = keyring.get_keyring()
            allowed_backends = {
                "keyring.backends.Windows", "keyring.backends.macOS", "keyring.backends.SecretService",
            }
            if (getattr(backend, "priority", 0) <= 0 or
                    type(backend).__module__ not in allowed_backends):
                raise RuntimeError("No secure credential backend")
            return keyring
        except (ImportError, RuntimeError) as error:
            raise AIConfigurationError(
                "Archivio credenziali sicuro non disponibile. Usa la variabile d'ambiente " + ENV_API_KEY + ".", 501
            ) from error

    def get(self) -> str | None:
        env_key = os.environ.get(ENV_API_KEY, "").strip()
        if env_key:
            return env_key
        try:
            return self._keyring().get_password(self.service, self.username)
        except AIConfigurationError:
            return None
        except Exception:
            return None

    def source(self) -> str | None:
        if os.environ.get(ENV_API_KEY, "").strip():
            return "ambiente"
        return "credenziali_sistema" if self.get() else None

    def save(self, key: object) -> None:
        if not isinstance(key, str) or not 12 <= len(key.strip()) <= 512 or "\n" in key:
            raise AIConfigurationError("Chiave API non valida.")
        try:
            self._keyring().set_password(self.service, self.username, key.strip())
        except AIConfigurationError:
            raise
        except Exception as error:
            raise AIConfigurationError("Impossibile salvare la chiave nelle credenziali di sistema.", 500) from error

    def delete(self) -> None:
        try:
            self._keyring().delete_password(self.service, self.username)
        except AIConfigurationError:
            raise
        except Exception:
            if self.source() != "ambiente":
                raise AIConfigurationError("Impossibile rimuovere la chiave dalle credenziali di sistema.", 500)
