"""Orchestrates one lead end to end. UI-independent so a CLI, API or CRM job can call it too."""
from __future__ import annotations

import logging
from typing import Callable
from urllib.parse import urlparse

from .config import Settings
from .crawler import crawl
from .discovery import name_tokens, suggest_websites
from .fetcher import Fetcher, FetchError
from .ingestion import is_reserved_host
from .llm import LLMClient, LLMError, generate_brief
from .models import Issue, Lead, LeadResult, Status
from .validation import needs_review, normalize, validate

log = logging.getLogger(__name__)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


def identity_issues(lead: Lead, pages) -> list[Issue]:
    issues = []
    home = pages[0]
    if home.final_url and _host(home.final_url) != _host(lead.website):
        issues.append(Issue(field="website", severity="error",
                            message=f"{lead.website} redirected to a different site: {home.final_url}"))
    tokens = name_tokens(lead.company_name)
    if tokens:
        site_text = normalize(" ".join(p.title + " " + p.text for p in pages))
        coverage = sum(t in site_text.replace("'", "") for t in tokens) / len(tokens)
        if coverage < 0.5:
            issues.append(Issue(field="website", severity="error",
                                message=f"company name '{lead.company_name}' barely appears on the site "
                                        f"({coverage:.0%} of name words); confirm this is the right company"))
    return issues


def process_lead(lead: Lead, fetcher: Fetcher, llm: LLMClient | None, settings: Settings,
                 search=None, use_search: bool = True) -> LeadResult:
    try:
        return _process(lead, fetcher, llm, settings, search, use_search)
    except Exception as exc:                       # one bad lead never stops the batch
        log.exception("unexpected failure for %s", lead.raw_input)
        return LeadResult(lead=lead, status=Status.ERROR, error_code="UNEXPECTED_ERROR",
                          error_message=f"{type(exc).__name__}: {exc}"[:400])


def _process(lead, fetcher, llm, settings, search, use_search) -> LeadResult:
    if not lead.website:
        candidates = suggest_websites(lead, search=search) if use_search else []
        message = "No website given. Confirm a suggested site or paste the official URL below."
        return LeadResult(lead=lead, status=Status.WEBSITE_NEEDED, candidates=candidates, error_message=message)

    if is_reserved_host(lead.website):
        return _failed(lead, "RESERVED_DOMAIN", f"{lead.website} uses a reserved test domain that can never resolve",
                       search, use_search)
    try:
        pages = crawl(lead.website, fetcher, max_pages=settings.max_pages, max_chars=settings.max_chars_per_page)
    except FetchError as exc:
        return _failed(lead, exc.code, exc.message, search, use_search)

    issues = identity_issues(lead, pages)
    useful = sum(len(p.text) for p in pages if p.text)
    if useful < settings.min_useful_chars:
        return LeadResult(lead=lead, status=Status.INSUFFICIENT_CONTENT, pages=pages, issues=issues,
                          error_code="LOW_TEXT",
                          error_message=f"only {useful} characters of readable text; the site may need JavaScript "
                                        "rendering or is a placeholder page")
    if llm is None:
        return LeadResult(lead=lead, status=Status.LLM_FAILED, pages=pages, issues=issues,
                          error_code="LLM_NOT_CONFIGURED", error_message="Set GEMINI_API_KEY to generate briefs")
    try:
        draft = generate_brief(llm, lead, [p for p in pages if p.text], settings.icp)
    except LLMError as exc:
        return LeadResult(lead=lead, status=Status.LLM_FAILED, pages=pages, issues=issues,
                          error_code=exc.code, error_message=exc.message, model=llm.model)

    draft, check_issues = validate(draft, pages)
    issues += check_issues
    status = Status.NEEDS_REVIEW if needs_review(draft, issues) else Status.READY
    return LeadResult(lead=lead, status=status, pages=pages, draft=draft, issues=issues, model=llm.model)


def _failed(lead: Lead, code: str, message: str, search, use_search: bool) -> LeadResult:
    """Fetch failure. When the domain itself is unusable, offer website suggestions for the company name."""
    candidates = []
    if code in {"DNS_NOT_RESOLVED", "RESERVED_DOMAIN"} and lead.company_name and use_search:
        given = _host(lead.website)
        candidates = [c for c in suggest_websites(lead, search=search) if _host(c["url"]) != given]
        if candidates:
            message += ". Possible official sites are suggested below."
    return LeadResult(lead=lead, status=Status.FETCH_FAILED, error_code=code, error_message=message,
                      candidates=candidates)


def process_batch(leads: list[Lead], fetcher: Fetcher, llm: LLMClient | None, settings: Settings,
                  on_result: Callable[[int, int, LeadResult], None] | None = None, **kwargs) -> list[LeadResult]:
    results = []
    for index, lead in enumerate(leads, 1):
        result = process_lead(lead, fetcher, llm, settings, **kwargs)
        results.append(result)
        if on_result:
            on_result(index, len(leads), result)
    return results
