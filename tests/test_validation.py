from conftest import COMMERCIAL, HOME, good_draft

from sia.crawler import crawl
from sia.models import BriefDraft
from sia.validation import needs_review, validate


def pages(site_fetcher):
    return crawl(HOME, site_fetcher)


def test_grounded_brief_passes(site_fetcher):
    draft, issues = validate(BriefDraft.model_validate(good_draft()), pages(site_fetcher))
    assert [i for i in issues if i.severity == "error"] == []
    assert not needs_review(draft, issues)


def test_fabricated_quote_is_rejected(site_fetcher):
    data = good_draft()
    data["company_overview"]["evidence"] = [{"source_url": HOME, "quote": "Family owned with 500 employees nationwide"}]
    draft, issues = validate(BriefDraft.model_validate(data), pages(site_fetcher))
    assert draft.company_overview.evidence == []
    assert any(i.field == "company_overview" and "no verified evidence" in i.message for i in issues)
    assert needs_review(draft, issues)


def test_b2b_without_evidence_is_downgraded(site_fetcher):
    data = good_draft()
    data["b2b_qualification"]["evidence"] = [{"source_url": HOME, "quote": "we only work with Fortune 500 firms"}]
    draft, issues = validate(BriefDraft.model_validate(data), pages(site_fetcher))
    assert draft.b2b_qualification.decision == "Insufficient evidence"


def test_no_decision_contradicted_by_site_text(site_fetcher):
    data = good_draft()
    data["b2b_qualification"]["decision"] = "No"
    _, issues = validate(BriefDraft.model_validate(data), pages(site_fetcher))
    assert any("site mentions" in i.message for i in issues)


def test_exactly_three_questions_required(site_fetcher):
    data = good_draft()
    data["discovery_questions"] = data["discovery_questions"][:2]
    draft, issues = validate(BriefDraft.model_validate(data), pages(site_fetcher))
    assert any("exactly 3" in i.message for i in issues)
    assert needs_review(draft, issues)


def test_quote_from_unfetched_url_is_rejected(site_fetcher):
    data = good_draft()
    data["core_offering"]["evidence"] = [{"source_url": "https://evil.example/", "quote": "a quote that is nowhere"}]
    draft, issues = validate(BriefDraft.model_validate(data), pages(site_fetcher))
    assert draft.core_offering.evidence == []
    assert any("quote not found" in i.message for i in issues)


def test_quote_cited_to_wrong_page_is_corrected(site_fetcher):
    data = good_draft()
    data["b2b_qualification"]["evidence"] = [{"source_url": HOME, "quote": "under annual maintenance agreements"}]
    draft, issues = validate(BriefDraft.model_validate(data), pages(site_fetcher))
    assert draft.b2b_qualification.evidence[0].source_url == COMMERCIAL
    assert any("citation corrected" in i.message for i in issues)


def test_quote_cited_to_unfetched_url_is_corrected_to_real_page(site_fetcher):
    data = good_draft()
    data["core_offering"]["evidence"] = [{"source_url": "https://evil.example/", "quote": "TPO, metal and flat roof systems"}]
    draft, _ = validate(BriefDraft.model_validate(data), pages(site_fetcher))
    assert draft.core_offering.evidence[0].source_url == HOME
