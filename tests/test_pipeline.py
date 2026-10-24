from conftest import HOME, FakeFetcher, FakeLLM, good_draft

from sia.config import Settings
from sia.models import Lead, Status
from sia.pipeline import process_batch, process_lead
from sia.repository import Repository
from sia.export import to_csv, to_json

SETTINGS = Settings(gemini_api_key="", db_path=":memory:")


def lead(**kw):
    return Lead(raw_input=kw.get("website") or kw.get("company_name", "x"), **kw)


def test_happy_path_is_ready(site_fetcher):
    llm = FakeLLM(good_draft())
    result = process_lead(lead(company_name="Lone Star Roofing", website=HOME, website_source="input"),
                          site_fetcher, llm, SETTINGS)
    assert result.status == Status.READY, result.issues
    assert result.draft.b2b_qualification.decision == "Yes"


def test_malformed_llm_output_is_retried_once(site_fetcher):
    llm = FakeLLM("not json", good_draft())
    result = process_lead(lead(website=HOME, website_source="input"), site_fetcher, llm, SETTINGS)
    assert llm.calls == 2 and result.draft is not None
    llm = FakeLLM("not json", "{}")
    result = process_lead(lead(website=HOME, website_source="input"), site_fetcher, llm, SETTINGS)
    assert result.status == Status.LLM_FAILED and result.error_code == "LLM_BAD_OUTPUT"


def test_batch_continues_after_failures():
    fetcher = FakeFetcher({HOME: "home.html", "https://dead-site.com/": "DNS_NOT_RESOLVED",
                           "https://spa-site.com/": "js_shell.html"})
    leads = [lead(website="https://dead-site.com/", website_source="input"),
             lead(company_name="Acme Roofing", location="Dallas, TX"),
             lead(website="https://spa-site.com/", website_source="input"),
             lead(company_name="Lone Star Roofing", website=HOME, website_source="input")]
    search = lambda q, n: [{"href": "https://www.yelp.com/biz/acme", "title": "Acme Roofing - Yelp", "body": ""},
                           {"href": "https://acmeroofingdallas.com/services", "title": "Acme Roofing & Construction | Dallas TX", "body": "Dallas roofing"}]
    results = process_batch(leads, fetcher, FakeLLM(good_draft()), SETTINGS, search=search)
    assert [r.status for r in results] == [Status.FETCH_FAILED, Status.WEBSITE_NEEDED,
                                           Status.INSUFFICIENT_CONTENT, Status.NEEDS_REVIEW]
    assert results[0].error_code == "DNS_NOT_RESOLVED"
    assert results[1].candidates[0]["url"] == "https://acmeroofingdallas.com/"     # directory site filtered out
    # the commercial page is missing from this site, so its citations cannot be verified
    assert results[3].draft.b2b_qualification.decision == "Insufficient evidence"


def test_identity_mismatch_needs_review(site_fetcher):
    result = process_lead(lead(company_name="Green Leaf Lawn Care", website=HOME, website_source="input"),
                          site_fetcher, FakeLLM(good_draft()), SETTINGS)
    assert result.status == Status.NEEDS_REVIEW
    assert any(i.field == "website" for i in result.issues)


def test_results_persist_with_separate_review(site_fetcher):
    repo = Repository(":memory:")
    result = process_lead(lead(company_name="Lone Star Roofing", website=HOME, website_source="input"),
                          site_fetcher, FakeLLM(good_draft()), SETTINGS)
    brief_id = repo.save_result(result)
    edited = result.draft.model_dump()
    edited["core_offering"]["text"] = "Edited by reviewer"
    repo.save_review(brief_id, "Approved", edited, reviewer="Suhas", notes="checked")
    row = repo.latest()[0]
    assert row["draft"]["core_offering"]["text"] != "Edited by reviewer"      # draft untouched
    assert row["final"]["core_offering"]["text"] == "Edited by reviewer"
    assert row["effective_status"] == "Approved"
    assert "Edited by reviewer" in to_csv([row]) and '"review_status": "Approved"' in to_json([row])


def test_reject_after_edit_keeps_edits_and_draft(site_fetcher):
    repo = Repository(":memory:")
    result = process_lead(lead(company_name="Lone Star Roofing", website=HOME, website_source="input"),
                          site_fetcher, FakeLLM(good_draft()), SETTINGS)
    brief_id = repo.save_result(result)
    edited = result.draft.model_dump()
    edited["target_audience"]["text"] = "Edited audience"
    repo.save_review(brief_id, "Approved", edited, reviewer="Suhas")
    row = repo.latest()[0]
    repo.save_review(brief_id, "Rejected", row["reviewed"], row["reviewer"], row["review_notes"] or "")
    row = repo.latest()[0]
    assert row["effective_status"] == "Rejected"
    assert row["status"] == result.status.value                                # pipeline status untouched
    assert row["final"]["target_audience"]["text"] == "Edited audience"
    assert row["draft"]["target_audience"]["text"] != "Edited audience"


def test_approve_without_edits_uses_ai_draft(site_fetcher):
    repo = Repository(":memory:")
    result = process_lead(lead(company_name="Lone Star Roofing", website=HOME, website_source="input"),
                          site_fetcher, FakeLLM(good_draft()), SETTINGS)
    brief_id = repo.save_result(result)
    repo.save_review(brief_id, "Approved", None)
    row = repo.latest()[0]
    assert row["effective_status"] == "Approved" and row["final"] == row["draft"]


def test_truncated_page_is_reported(site_fetcher):
    real_get = site_fetcher.get

    def get(url):
        resp = real_get(url)
        resp.truncated = url == HOME
        return resp

    site_fetcher.get = get
    result = process_lead(lead(company_name="Lone Star Roofing", website=HOME, website_source="input"),
                          site_fetcher, FakeLLM(good_draft()), SETTINGS)
    assert any("only the first part was read" in i.message for i in result.issues)


def test_retired_gemini_model_switches_automatically():
    from types import SimpleNamespace
    from google.genai import errors
    from sia.llm import GeminiClient

    class Models:
        def __init__(self):
            self.used = []

        def generate_content(self, model, contents, config):
            self.used.append(model)
            if model == "gemini-2.5-flash":
                raise errors.ClientError(404, {"error": {"code": 404, "message":
                    "This model models/gemini-2.5-flash is no longer available. Please use models/gemini-3.8-flash"}})
            return SimpleNamespace(text="{}")

        def list(self):
            return []

    client = GeminiClient("key", "gemini-2.5-flash", sleep=lambda s: None)
    client._client = SimpleNamespace(models=Models())
    assert client.generate("s", "p", {}) == "{}"
    assert client.model == "gemini-3.8-flash"


def test_reserved_domain_fails_fast_and_suggests_sites():
    fetcher = FakeFetcher({})
    search = lambda q, n: [{"href": "https://northstarsolar.com/", "title": "Northstar Solar Solutions | Sacramento", "body": "Sacramento solar"}]
    result = process_lead(lead(company_name="Northstar Solar Solutions", location="Sacramento, CA",
                               website="https://northstarsolarsolutions.example/", website_source="input"),
                          fetcher, FakeLLM(), SETTINGS, search=search)
    assert result.status == Status.FETCH_FAILED and result.error_code == "RESERVED_DOMAIN"
    assert fetcher.requested == []                                  # never tried to fetch
    assert result.candidates[0]["url"] == "https://northstarsolar.com/"


def test_confirming_a_website_that_is_already_saved_merges_leads():
    from sia.models import LeadResult
    repo = Repository(":memory:")
    repo.save_result(LeadResult(lead=lead(website=HOME, website_source="input"), status=Status.READY))
    named = lead(company_name="Lone Star Roofing", location="Houston, TX")
    repo.save_result(LeadResult(lead=named, status=Status.WEBSITE_NEEDED))
    name_row = [r for r in repo.latest() if r["company_name"]][0]
    named.website, named.website_source = HOME, "manual"
    repo.save_result(LeadResult(lead=named, status=Status.READY), name_row["lead_id"])
    rows = repo.latest()
    assert len(rows) == 1 and rows[0]["company_name"] == "Lone Star Roofing"
