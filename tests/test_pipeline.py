from conftest import HOME, FakeFetcher, FakeLLM, good_draft

from sia.config import Settings
from sia.models import Lead, Status
from sia.pipeline import process_batch, process_lead
from sia.repository import Repository
from sia.export import to_csv, to_hubspot_csv, to_json

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
    results = process_batch(leads, fetcher, FakeLLM(good_draft(), good_draft()), SETTINGS, search=search)
    assert [r.status for r in results] == [Status.FETCH_FAILED, Status.WEBSITE_NEEDED,
                                           Status.INSUFFICIENT_CONTENT, Status.NEEDS_REVIEW]
    assert results[0].error_code == "DNS_NOT_RESOLVED"
    assert results[1].candidates[0]["url"] == "https://acmeroofingdallas.com/"     # directory site filtered out
    assert results[1].draft is not None                                            # preliminary brief from snippets
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


TURF_RESULTS = [
    {"href": "https://www.yelp.com/biz/brightplay-turf-chicago",
     "title": "BrightPlay Turf - Chicago, IL - Yelp",
     "body": "BrightPlay Turf installs artificial turf for schools, parks and commercial properties in Chicago."},
    {"href": "https://www.brightplayturf.com/", "title": "BrightPlay Turf | Artificial Turf Chicago",
     "body": "BrightPlay Turf designs pet-friendly lawns and sports fields for homeowners and businesses in Chicago."},
    {"href": "https://www.yelp.com/biz/brightplay-turf-austin", "title": "BrightPlay Turf - Austin, TX",
     "body": "Artificial grass in Austin."},                                  # other city: never used
]


def snippet_draft():
    ev = {"source_url": "https://www.yelp.com/biz/brightplay-turf-chicago",
          "quote": "installs artificial turf for schools, parks and commercial properties in Chicago"}
    ev2 = {"source_url": "https://www.brightplayturf.com/",
           "quote": "designs pet-friendly lawns and sports fields for homeowners and businesses"}
    return {
        "company_overview": {"text": "Chicago company that installs artificial turf.", "evidence": [ev]},
        "core_offering": {"text": "Artificial turf, pet-friendly lawns and sports fields.", "evidence": [ev2]},
        "target_audience": {"text": "Schools, parks, commercial properties, homeowners and businesses.", "evidence": [ev, ev2]},
        "b2b_qualification": {"decision": "Yes", "rationale": "Installs turf for schools and commercial properties.",
                              "evidence": [ev]},
        "discovery_questions": [
            {"question": "Which share of your installs are schools and parks versus commercial properties?", "based_on": ev},
            {"question": "How do you scope sports field projects compared with pet-friendly lawns?", "based_on": ev2},
            {"question": "What do businesses ask about most before choosing artificial turf over sod?", "based_on": ev2},
        ],
    }


def test_name_only_lead_gets_preliminary_brief_from_search():
    llm = FakeLLM(snippet_draft())
    result = process_lead(lead(company_name="BrightPlay Turf", location="Chicago, IL"), FakeFetcher({}), llm,
                          SETTINGS, search=lambda q, n: TURF_RESULTS)
    assert result.status == Status.WEBSITE_NEEDED                     # still needs a confirmed website
    assert result.draft.b2b_qualification.decision == "Yes"
    assert [p.method for p in result.pages] == ["search_snippet", "search_snippet"]   # Austin result dropped
    assert "preliminary" in result.issues[0].message
    assert result.candidates[0]["url"] == "https://www.brightplayturf.com/"


def test_name_only_lead_without_matching_snippets_makes_no_brief():
    llm = FakeLLM()
    result = process_lead(lead(company_name="Maple Street Auto Repair", location="Madison, WI"), FakeFetcher({}),
                          llm, SETTINGS, search=lambda q, n: TURF_RESULTS)
    assert result.draft is None and llm.calls == 0
    assert "found nothing clearly about this company" in result.error_message


def test_search_prompt_warns_model_about_snippets():
    from sia.discovery import snippet_pages
    from sia.llm import build_prompt
    turf = lead(company_name="BrightPlay Turf", location="Chicago, IL")
    prompt = build_prompt(turf, snippet_pages(turf, TURF_RESULTS), SETTINGS.icp)
    assert "web search result" in prompt and "Search result snippets:" in prompt


def test_hubspot_export_maps_brief_to_company_properties(site_fetcher):
    repo = Repository(":memory:")
    result = process_lead(lead(company_name="Lone Star Roofing", location="Houston, TX, USA", website=HOME,
                               website_source="input"), site_fetcher, FakeLLM(good_draft()), SETTINGS)
    repo.save_result(result)
    header, line = to_hubspot_csv(repo.latest()).splitlines()[:2]
    assert header.startswith("Company name,Company Domain Name,City,State/Region,Description")
    assert "lonestarroofing.com,Houston,TX," in line


def test_similar_company_names_are_not_used_for_a_brief():
    """Real search results from the assignment run: each is a different, similarly named business."""
    from sia.discovery import snippet_pages
    cases = [
        ("Sunrise Plumbing & Drain Services", "Tampa, FL",
         "Plumber in Tampa, FL | Sunrise Plumbing Team", "Sunrise Plumbing Team offers drain cleaning in Tampa."),
        ("Green Leaf Lawn Care", "Columbus, OH",
         "Greenleaf Lawns - Columbus, OH - Yelp", "At Greenleaf Lawns LLC, we take pride in lawn care services."),
        ("Blue Ridge HVAC Services", "Roanoke, VA",
         "Blue Ridge Heating & Air - Christiansburg, VA", "Serving the Roanoke Valley since 1989."),
    ]
    for name, location, title, body in cases:
        pages = snippet_pages(lead(company_name=name, location=location),
                              [{"href": "https://x.com/", "title": title, "body": body}])
        assert pages == [], name


def test_name_phrase_allows_small_spelling_differences():
    from sia.discovery import name_pattern
    assert name_pattern("Green Leaf Lawn Care").search("greenleaf lawn care of columbus")
    assert name_pattern("Sunrise Plumbing & Drain Services").search("sunrise plumbing and drain, tampa")
    assert name_pattern("Joe's Backyard Landscaping").search("joes backyard landscaping llc")


def test_snippet_brief_that_states_nothing_is_dropped():
    empty = snippet_draft()
    for key in ("company_overview", "core_offering", "target_audience"):
        empty[key] = {"text": "Not stated on the website", "evidence": []}
    result = process_lead(lead(company_name="BrightPlay Turf", location="Chicago, IL"), FakeFetcher({}),
                          FakeLLM(empty), SETTINGS, search=lambda q, n: TURF_RESULTS)
    assert result.draft is None and "not clearly about this company" in result.error_message


def test_export_names_url_only_leads_from_the_homepage_title():
    from sia.export import company_name
    row = {"company_name": "", "website": "https://www.springhilllandscaping.com/",
           "pages": [{"title": "Home - King of the Hill"}]}
    assert company_name(row) == "King of the Hill"
    row = {"company_name": "", "website": "https://www.houstonroofingonline.com/",
           "pages": [{"title": "Houston Roofing | Expert Roof Replacement & Repairs"}]}
    assert company_name(row) == "Houston Roofing"
