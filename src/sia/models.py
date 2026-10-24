"""Typed records shared by every stage of the pipeline."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Status(str, Enum):
    WEBSITE_NEEDED = "Website needed"      # name-only lead, no confirmed site yet
    FETCH_FAILED = "Fetch failed"          # DNS, timeout, 404, blocked ...
    INSUFFICIENT_CONTENT = "Insufficient content"
    LLM_FAILED = "LLM failed"
    ERROR = "Error"                        # unexpected failure; batch continues
    NEEDS_REVIEW = "Needs review"          # brief produced, validation raised issues
    READY = "Ready"                        # brief produced, all checks passed
    APPROVED = "Approved"
    REJECTED = "Rejected"


class Lead(BaseModel):
    raw_input: str
    company_name: str = ""
    location: str = ""
    descriptor: str = ""                   # e.g. "Artificial Turf & Landscaping"
    website: str = ""                      # normalised https://host/ or ""
    website_source: Literal["input", "search_confirmed", "manual", ""] = ""

    @property
    def key(self) -> str:
        from urllib.parse import urlparse
        if self.website:
            return (urlparse(self.website).hostname or "").removeprefix("www.")
        return f"{self.company_name.lower()}|{self.location.lower()}"

    @property
    def display_name(self) -> str:
        return self.company_name or self.key


class Page(BaseModel):
    url: str
    final_url: str = ""
    status_code: int | None = None
    title: str = ""
    text: str = ""
    method: str = ""                       # trafilatura | fallback
    truncated: bool = False                # HTML was larger than MAX_BYTES and was cut
    fetched_at: str = Field(default_factory=utcnow)
    error: str = ""


# ---- LLM output schema (what Gemini must return) ----

class Evidence(BaseModel):
    source_url: str = Field(description="One of the page URLs provided in the input")
    quote: str = Field(description="Exact sentence or phrase copied verbatim from that page")


class Claim(BaseModel):
    text: str
    evidence: list[Evidence] = Field(default_factory=list)


class B2BDecision(BaseModel):
    decision: Literal["Yes", "No", "Insufficient evidence"]
    rationale: str
    evidence: list[Evidence] = Field(default_factory=list)


class DiscoveryQuestion(BaseModel):
    question: str
    based_on: Evidence


class BriefDraft(BaseModel):
    company_overview: Claim
    core_offering: Claim
    target_audience: Claim
    b2b_qualification: B2BDecision
    discovery_questions: list[DiscoveryQuestion]


class Issue(BaseModel):
    field: str
    severity: Literal["error", "warning"]
    message: str


class LeadResult(BaseModel):
    lead: Lead
    status: Status
    pages: list[Page] = Field(default_factory=list)
    draft: BriefDraft | None = None
    issues: list[Issue] = Field(default_factory=list)
    error_code: str = ""
    error_message: str = ""
    candidates: list[dict] = Field(default_factory=list)   # website suggestions for name-only leads
    model: str = ""
    created_at: str = Field(default_factory=utcnow)
