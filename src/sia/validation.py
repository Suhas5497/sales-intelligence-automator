"""Deterministic checks on the LLM draft. A brief is only "Ready" when every factual claim is backed by
a quote that really appears in the scraped text of a page we fetched."""
from __future__ import annotations

import re

from .models import BriefDraft, Evidence, Issue, Page

NOT_STATED = re.compile(r"^not stated", re.I)
BUSINESS_TERMS = re.compile(
    r"\b(commercial|businesses|business owners|b2b|property managers?|property management|contractors?|"
    r"general contractors|builders|developers|offices?|corporate|wholesale|trade accounts?|institutions?|"
    r"municipal|hoas?|facilit(y|ies) managers?|restaurants|retailers|industrial|enterprise|schools|hospitals)\b", re.I)
GENERIC_QUESTIONS = re.compile(
    r"(biggest challenges?|what are your (goals|pain points)|tell me (more )?about your (business|company)|"
    r"how can we help|what keeps you up)", re.I)
MIN_QUOTE_CHARS = 8
NUMBER = re.compile(r"\d[\d,.]*\d|\d")
FILLER = {"with", "from", "that", "this", "their", "they", "them", "also", "have", "which", "such", "other", "into",
          "including", "company", "companies", "provides", "provide", "offers", "offer", "services", "service",
          "based", "stated", "website", "across", "both", "well", "variety", "range"}


def normalize(text: str) -> str:
    text = (text or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-").replace(" ", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).casefold().strip()


def _url_key(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", (url or "").strip()).rstrip("/").lower()


class EvidenceChecker:
    def __init__(self, pages: list[Page]):
        self.pages = {}                                  # url key -> normalized page text
        self.sources = []                                # (url to cite, normalized text), one per page
        for page in pages:
            if page.text:
                text = normalize(page.text)
                self.sources.append((page.final_url or page.url, text))
                for url in {page.url, page.final_url} - {""}:
                    self.pages[_url_key(url)] = text
        self.all_text = " ".join(text for _, text in self.sources)

    def check(self, ev: Evidence, field: str) -> tuple[bool, list[Issue]]:
        """Return (grounded, issues) for one evidence item.

        A quote must appear word for word on a single fetched page. If it is on a different page than the one
        cited, the citation is corrected to that page so the brief never links to the wrong source."""
        quote = normalize(ev.quote).strip(" .\"'")
        if len(quote) < MIN_QUOTE_CHARS:
            return False, [Issue(field=field, severity="error", message=f"evidence quote too short: '{ev.quote}'")]
        if quote in self.pages.get(_url_key(ev.source_url), ""):
            return True, []
        for url, text in self.sources:
            if quote in text:
                cited, ev.source_url = ev.source_url, url
                return True, [Issue(field=field, severity="warning",
                                    message=f"quote was cited to {cited} but found on {url}; citation corrected")]
        return False, [Issue(field=field, severity="error", message=f"quote not found in scraped text: '{ev.quote[:120]}'")]


def _stems(text: str) -> set[str]:
    """Content words cut to 4 letters, so roof/roofs/roofing or install/installs/installation match."""
    return {w[:4] for w in re.findall(r"[a-z]{4,}", normalize(text)) if w not in FILLER}


def support_issues(text: str, evidence: list[Evidence], field: str, all_text: str,
                   overlap_severity: str = "error") -> list[Issue]:
    """Second check, after each quote is proven to exist: does the evidence actually back the sentence?
    Numbers (years, counts, prices) must appear on a fetched page, and the sentence must share key words
    with its quotes. Catches a real quote attached to an overstated or invented claim."""
    issues = []
    invented = [n for n in NUMBER.findall(text) if n not in all_text]
    if invented:
        issues.append(Issue(field=field, severity="error",
                            message=f"states {', '.join(invented)}, which does not appear on any fetched page"))
    if evidence and not NOT_STATED.match(text.strip()):
        quotes = set().union(*(_stems(ev.quote) for ev in evidence))
        if _stems(text) and not _stems(text) & quotes:
            issues.append(Issue(field=field, severity=overlap_severity,
                                message="the quoted evidence shares no key words with this statement; "
                                        "check that it really supports it"))
    return issues


def validate(draft: BriefDraft, pages: list[Page]) -> tuple[BriefDraft, list[Issue]]:
    """Return a possibly corrected copy of the draft and all issues found."""
    draft = draft.model_copy(deep=True)
    checker = EvidenceChecker(pages)
    issues: list[Issue] = []

    for name in ("company_overview", "core_offering", "target_audience"):
        claim = getattr(draft, name)
        grounded = []
        for ev in claim.evidence:
            ok, found = checker.check(ev, name)
            issues += found
            if ok:
                grounded.append(ev)
        claim.evidence = grounded                       # keep only verified evidence
        issues += support_issues(claim.text, grounded, name, checker.all_text)
        if NOT_STATED.match(claim.text.strip()):
            issues.append(Issue(field=name, severity="warning", message="not stated on the website"))
        elif not grounded:
            issues.append(Issue(field=name, severity="error", message="claim has no verified evidence"))

    b2b = draft.b2b_qualification
    grounded = [ev for ev in b2b.evidence if _keep(checker, ev, "b2b_qualification", issues)]
    b2b.evidence = grounded
    # the rationale is reasoning, not a copied fact, so low word overlap is only a warning there
    issues += support_issues(b2b.rationale, grounded, "b2b_qualification", checker.all_text, "warning")
    if b2b.decision != "Insufficient evidence" and not grounded:
        issues.append(Issue(field="b2b_qualification", severity="error",
                            message=f"decision '{b2b.decision}' had no verified evidence; downgraded to 'Insufficient evidence'"))
        b2b.decision = "Insufficient evidence"
    terms = sorted({m.group(0).lower() for m in BUSINESS_TERMS.finditer(checker.all_text)})
    if b2b.decision == "No" and len(terms) >= 2:
        issues.append(Issue(field="b2b_qualification", severity="error",
                            message=f"decision is 'No' but the site mentions: {', '.join(terms[:6])}"))
    if b2b.decision == "Yes" and not terms:
        issues.append(Issue(field="b2b_qualification", severity="warning",
                            message="decision is 'Yes' but no business-customer keywords were found on the site"))

    questions = draft.discovery_questions
    if len(questions) != 3:
        issues.append(Issue(field="discovery_questions", severity="error",
                            message=f"expected exactly 3 questions, got {len(questions)}"))
    seen = set()
    for i, q in enumerate(questions, 1):
        field = f"discovery_questions[{i}]"
        text = q.question.strip()
        if normalize(text) in seen:
            issues.append(Issue(field=field, severity="error", message="duplicate question"))
        seen.add(normalize(text))
        if not text.endswith("?") or len(text) < 25:
            issues.append(Issue(field=field, severity="warning", message="question is too short or not a question"))
        if GENERIC_QUESTIONS.search(text):
            issues.append(Issue(field=field, severity="warning", message="question looks generic"))
        ok, found = checker.check(q.based_on, field)
        if not ok:
            issues.append(Issue(field=field, severity="error", message="question is not tied to verified website text"))
        elif not _stems(text) & _stems(q.based_on.quote):
            issues.append(Issue(field=field, severity="warning",
                                message="question shares no key words with the text it is based on"))
        issues += [i for i in found if i.severity == "warning"]
    return draft, issues


def _keep(checker: EvidenceChecker, ev: Evidence, field: str, issues: list[Issue]) -> bool:
    ok, found = checker.check(ev, field)
    issues += found
    return ok


def needs_review(draft: BriefDraft, issues: list[Issue]) -> bool:
    return any(i.severity == "error" for i in issues) or draft.b2b_qualification.decision == "Insufficient evidence"
