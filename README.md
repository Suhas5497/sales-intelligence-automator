# Sales Intelligence Automator

Turns a list of leads (website URLs, or company name + city) into **evidence-backed pre-call sales briefs**:
company overview, core product/service, target customer, a B2B qualification decision with rationale, and
three discovery questions. Every claim cites a sentence copied from the company's own website, and Python
verifies that sentence really exists in the scraped text before the brief is marked **Ready**.
A reviewer can confirm websites, edit, approve or reject briefs, and export them as CSV, JSON or a HubSpot
import file.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # Windows: copy .env.example .env
# put your free Gemini key in .env:  GEMINI_API_KEY=...   (https://aistudio.google.com/apikey)
streamlit run app.py
```

In **GitHub Codespaces** the dev container (`.devcontainer/devcontainer.json`) installs everything. Add `GEMINI_API_KEY` as a Codespaces secret
(or in `.env`), then run `streamlit run app.py`; port 8501 opens automatically.

Headless run over the 15 assignment leads (writes SQLite + `output/sales_briefs.csv/json` and
`output/hubspot_companies.csv`):

```bash
python run_batch.py sample_data/sample_leads.txt
python run_batch.py sample_data/sample_leads.txt --retry-failed   # re-run only "LLM failed" leads (Gemini 503 / quota)
```

Tests (no network, no API key; saved HTML fixtures and a fake LLM):

```bash
python -m pytest -q
```

## Using the web interface

The app is a workspace: navigation in the left sidebar, the list of leads in the middle, and the selected
lead's brief on the right.

1. **Add leads**: paste one lead per line (`https://…` or `Company – Descriptor, City ST`) or upload a CSV with
   `company_name, website, location` columns (or a single `lead` column). Duplicates are shown and skipped.
   Click **Run analysis**; each lead shows its status as it finishes, and a failing lead never stops the batch.
2. **Briefs**: filter by status with the chips, search by name, and click a lead. The brief shows the
   overview, core service, target customer, B2B decision with rationale, and three discovery questions with
   the website text each one is based on. The **Evidence** panel shows the scraped page with every verified
   quote highlighted, so a reviewer can see exactly where each claim came from.
3. **Review queue**: the same workspace, showing only leads that need a person (Needs review, Website
   needed, Failed). For name-only or broken-URL leads, pick a suggested website or paste one and re-run.
   When web search found snippets about the company, a **preliminary brief** is shown meanwhile; it is
   labelled as such and cannot be approved until the website is confirmed.
   **Edit** opens a form to correct any field; **Approve** / **Reject** record the decision. Edits are stored
   separately; the generated draft is never overwritten.
4. **Export**: CSV for the sales team, JSON with the full draft, reviewed version and evidence, and a
   **HubSpot import CSV** (standard company properties + brief fields, de-duplicated by domain); optionally
   approved briefs only.
5. **Settings & ICP** (sidebar): edit the ideal customer profile used for the B2B decision, the number of
   pages read per site, and website suggestions.

Status meanings: 🟢 Ready (all checks passed) · 🟡 Needs review (a check failed, or B2B is "Insufficient
evidence") · 🔎 Website needed · 🔴 Fetch failed / LLM failed · 🟠 Insufficient content (empty or
JavaScript-only page) · ✅ Approved · ⛔ Rejected.

## Project structure

```
app.py                  Streamlit UI (workspace: add leads, briefs, review queue, export)
.streamlit/config.toml  UI theme (blue accent)
run_batch.py            same pipeline from the command line
src/sia/
  config.py             settings from env / .env, default ICP text
  models.py             Pydantic records: Lead, Page, BriefDraft (LLM schema), Issue, LeadResult
  ingestion.py          paste/CSV parsing, URL normalisation, name/location split, de-duplication
  safety.py             only public http(s) hosts; blocks localhost/private IPs (also after redirects)
  fetcher.py            requests with timeouts, bounded retries, Retry-After, size cap, CAPTCHA detection
  extractor.py          trafilatura main text + BeautifulSoup fallback, boilerplate filters, link picker
  crawler.py            homepage + up to 3 useful same-site pages; drops text repeated across pages
  discovery.py          web search for name-only leads: website suggestions + snippets for a preliminary brief
  llm.py                prompt, Gemini client (JSON-schema output, rate-limit backoff), one repair retry
  validation.py         deterministic checks on the draft (quotes, citations, claim support, B2B, questions)
  pipeline.py           one lead end to end, error isolation, identity check, batch runner
  repository.py         SQLite: leads, brief history, reviewer edits
  export.py             CSV / JSON / HubSpot company import
  highlight.py          marks verified quotes in page text for the evidence panel
tests/                  pytest suite with HTML fixtures and a fake LLM
sample_data/            the 15 assignment leads (.txt) and a CSV example
sample_output/          exported briefs from a run over the 15 assignment leads (CSV + JSON)
.devcontainer/          Codespaces configuration
```

Dependencies: `streamlit`, `pandas`, `requests`, `trafilatura`, `beautifulsoup4`, `lxml`, `pydantic`,
`python-dotenv`, `google-genai`, `ddgs`, `pytest` (see `requirements.txt`). Python 3.10+.

## Design notes

**Architecture and tools.** The pipeline is plain Python in `src/sia` and knows nothing about the UI, so the
same `process_lead()` runs from Streamlit, the CLI, or later a REST endpoint or CRM job. Each lead flows
through ingest → safe fetch → bounded crawl (homepage plus up to three About / Services / Commercial pages)
→ main-text extraction → one Gemini call → deterministic validation → SQLite, and every stage returns a typed
Pydantic record. `requests` + `trafilatura` (with a BeautifulSoup fallback) were chosen because small-business
sites are mostly static HTML: they are fast, free and strip menus, footers and cookie banners, and lines
repeated across a site's pages are dropped as boilerplate. Gemini Flash (free tier) needs no GPU and returns
schema-checked JSON; Streamlit + SQLite keep the prototype a single process with no infrastructure. Results
are stored as a history per lead with reviewer edits kept separately, and the Export page writes a HubSpot
company-import file, which is the shape a CRM integration needs.

**Edge cases and keeping the LLM strict.** Every failure becomes a status with an error code instead of an
exception (DNS, timeouts, 403/404/429/5xx with bounded retries, CAPTCHA pages reported but never bypassed,
non-HTML, oversized pages, JavaScript-only pages flagged as "Insufficient content", redirects to private
addresses or another company), so one bad site never stops a batch. Name-only leads are searched on the web:
website suggestions always need a person to confirm them, and snippets that clearly match the company's name
and city produce a clearly labelled *preliminary* brief until the official site is confirmed. The LLM only
interprets evidence: it must cite a verbatim quote and URL for every claim and question, treat page text as
untrusted data, and answer "Not stated" rather than guess. Python then checks that each quote exists on the
page it cites (correcting the link if it is on another page, dropping it if it is on none), that numbers in a
claim appear on the site and the quote shares key words with the claim it supports, that Yes/No B2B
decisions have verified evidence (otherwise "Insufficient evidence") and are not contradicted by the site,
that there are three distinct non-generic questions, and that the site names the company. Any error sends
the brief to **Needs review**; only fully verified briefs are **Ready**.

**What I would improve with more time.** Better website discovery for name-only leads (a paid search API
plus Google Business Profile), a Playwright fallback for JavaScript sites, a small hand-labelled set of leads
to measure brief accuracy and B2B decisions for each prompt version, semantic matching for paraphrased
evidence, concurrent crawling with per-domain rate limits and robots.txt, and a REST API with
authentication, PostgreSQL and two-way HubSpot / Salesforce sync.

## Implementation details

**Validation rules** (`validation.py`): quote of at least 8 characters found on a single fetched page after
whitespace/quote normalisation; citation corrected to the page that really contains the quote; claims with
no verified evidence are errors; numbers in a claim or rationale must appear on a fetched page; a claim must
share key words with its quotes (a warning for the B2B rationale and questions, which are reasoning rather
than copied facts); a "No" decision on a site mentioning commercial clients, property managers or
contractors is flagged; exactly three distinct questions, each tied to verified text. Malformed JSON gets one
repair retry, then `LLM_BAD_OUTPUT`. Temperature is 0.1 and the prompt version is stored with each brief.

**Name-only leads** (`discovery.py`): one DuckDuckGo search per lead. Suggested websites exclude directories
(Yelp, BBB, Facebook …) and are scored on name words in the domain and title plus the city. Snippets used for
a preliminary brief must contain most of the company's name words and the city; directory listings are
allowed there because they describe the business. A preliminary brief cannot be approved until the website
is confirmed, which replaces it with a verified brief.

**URL safety (SSRF).** Only `http(s)` URLs are fetched; the host is resolved and refused if any address is
private, loopback, link-local or otherwise non-public, and the check is repeated on every redirect hop.
Known limitation: `requests` resolves the name again when it connects, so a DNS-rebinding attacker who
controls a domain could in theory swap in a private address between the check and the connection. Closing
that fully means pinning the connection to the checked IP or fetching through an egress proxy that blocks
private ranges.

**The B2B rule (assumption).** The assignment does not define the ICP, so the default treats a lead as B2B when
its site shows it serves businesses or organisations; it is editable in the sidebar or with
`ICP_DESCRIPTION` in `.env`. The decision is Yes / No, plus "Insufficient evidence" when the site does not
say enough: a guessed Yes or No would mislead a sales rep, so that case goes to a person.

## Where the LLM is used

Only in `src/sia/llm.py`, once per lead (plus at most one repair call), to turn already-extracted page text
into the brief. Fetching, cleaning, validation, status decisions and storage are deterministic Python.

- **Model:** Google Gemini Flash (`GEMINI_MODEL`, default `gemini-3.8-flash`) through the free tier of the
  Gemini API, with JSON-schema output and temperature 0.1.
- **Why cloud, not local:** no GPU or install needed, reliable structured JSON, fast enough for interactive
  batches, and free. A local model (e.g. Ollama) would keep data on the machine but is slower and less
  reliable at strict JSON on ordinary hardware; only public website text is sent.
- **Limitations:** free-tier rate limits (handled with backoff and an `LLM_RATE_LIMITED` status); page text
  leaves the machine; Google retires model names (the client switches to a current Flash model
  automatically); exact-quote validation rejects paraphrased evidence, which sends those briefs to review
  rather than accepting them.

## Results on the assignment leads

All 15 leads from the assignment were processed (`sample_output/`):

| Result | Leads |
|---|---|
| Ready (all checks passed, B2B: Yes) | houstonroofingonline.com, springhilllandscaping.com, denverlockandsafe.com, piedmontmoving.com |
| Needs review | redtruckbakery.com (redirects to a different company, classiccatering.com) |
| Fetch failed | centraltexasbarkery.com (domain does not resolve), bostonplumbing.com (no response) |
| Website needed | the 8 name-only leads; suggested websites are shown for a reviewer to confirm |
