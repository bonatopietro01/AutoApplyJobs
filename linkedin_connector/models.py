"""Small data models shared by the LinkedIn reader and later phases."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class LinkedInJob:
    """One job that is visible in the user's LinkedIn saved-jobs list."""

    linkedin_job_id: str
    title: str
    company: str | None
    location: str | None
    linkedin_url: str
    description: str | None = None
    description_complete: bool = False

    def to_dict(self) -> dict[str, str | bool | None]:
        """Return a JSON-ready representation without persisting it."""
        return asdict(self)
