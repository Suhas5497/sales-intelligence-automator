# Sales Intelligence Automator

Turns a list of leads (website URLs, or company name + city) into **evidence-backed pre-call sales briefs**:
company overview, core product/service, target customer, a B2B qualification decision with rationale, and
three discovery questions. Every claim cites a sentence copied from the company's own website, and Python
verifies that sentence really exists in the scraped text before the brief is marked **Ready**.
A reviewer can confirm websites, edit, approve or reject briefs, and export them as CSV or JSON.

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

Headless run over the 15 assignment leads (writes SQLite + `output/sales_briefs.csv/json`):

```bash
python run_batch.py sample_data/sample_leads.txt
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
   **Edit** opens a form to correct any field; **Approve** / **Reject** record the decision. Edits are stored
   separately; the generated draft is never overwritten.
4. **Export**: CSV for the sales team / CRM import, JSON with the full draft, reviewed version and evidence,
   optionally approved briefs only.
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
  discovery.py          website suggestions for name-only leads (DuckDuckGo), directory sites filtered
  llm.py                prompt, Gemini client (JSON-schema output, rate-limit backoff), one repair retry
  validation.py         deterministic checks on the draft (quotes, URLs, B2B, questions)
  pipeline.py           one lead end to end, error isolation, identity check, batch runner
  repository.py         SQLite: leads, brief history, reviewer edits
  export.py             CSV / JSON
  highlight.py          marks verified quotes in page text for the evidence panel
tests/                  pytest suite with HTML fixtures and a fake LLM
sample_data/            the 15 assignment leads (.txt) and a CSV example
sample_output/          exported briefs from a run over the 15 assignment leads (CSV + JSON)
.devcontainer/          Codespaces configuration
```

Dependencies: `streamlit`, `pandas`, `requests`, `trafilatura`, `beautifulsoup4`, `lxml`, `pydantic`,
`python-dotenv`, `google-genai`, `ddgs`, `pytest` (see `requirements.txt`). Python 3.10+.

## Design notes

**Architecture.** The pipeline is plain Python in `src/sia` and knows nothing about Streamlit, so the same
`process_lead()` can run from the UI, the CLI, a REST endpoint or a CRM webhook. Each lead flows through
ingest → (website suggestion if name-only) → safe fetch → bounded crawl (homepage plus up to three pages
whose links look like About / Services / Commercial) → main-text extraction → Gemini → deterministic
validation → SQLite. Every stage returns a typed record, and every failure becomes a status with an error
code (`DNS_NOT_RESOLVED`, `HTTP_403`, `BLOCKED_BY_CHALLENGE`, `LOW_TEXT`, `LLM_RATE_LIMITED` …) instead of an
exception, so one bad website never stops a batch. Results are stored as a history per lead, with reviewer
edits in separate columns, which is the shape a CRM sync would need.

**Tool choices.** `requests` + `trafilatura` handle static small-business sites quickly and cheaply;
trafilatura removes menus, footers and cookie banners, a BeautifulSoup fallback strips elements whose
id/class look like navigation, consent or footer blocks, and lines repeated on several pages of the same site
(marketing boilerplate) are dropped. Playwright was deliberately left out: pages that yield almost no text are
flagged "Insufficient content (may need JavaScript rendering)" for a human instead of slowing every run.
Gemini Flash (cloud, free tier; `GEMINI_MODEL`, default `gemini-3.8-flash`, and the client switches to a current Flash model automatically if Google retires the configured one) was chosen because it needs no GPU, supports JSON-schema output and is
fast enough for interactive batches; limits are the free-tier rate quota (handled with backoff and a clear
`LLM_RATE_LIMITED` status) and that page text leaves the machine. Streamlit and SQLite keep the prototype a
single Python process with zero infrastructure. Name-only leads get DuckDuckGo suggestions scored on name
words in the domain/title and the city in the snippet, but a suggestion is **never used until a person
confirms it**: guessing the wrong company is worse than asking.

**Keeping the LLM strict.** The model is told to use only the supplied pages, to treat page text as
untrusted data (prompt-injection guard), to return "Not stated on the website" rather than guess, and to
attach a verbatim quote + URL to every claim and question. Python then re-checks everything: each quote must
appear (after whitespace/quote normalisation) in the text of a single page we actually fetched; a quote found
on a different page than the one cited has its link corrected to that page, so a brief never points to the
wrong source; unverified evidence is removed; a claim left with no evidence is an error; a Yes/No B2B decision without verified evidence is
downgraded to "Insufficient evidence"; a "No" on a site that mentions commercial clients, property managers,
contractors etc. is flagged as contradictory; there must be exactly three distinct, non-generic questions,
each tied to verified text; and the site must actually mention the company name (catches wrong-company
URLs and redirects). Any error puts the brief in **Needs review**. Malformed JSON gets one repair retry, then
`LLM_BAD_OUTPUT`. Temperature is 0.1 and the prompt version is stored with each brief.

**Edge cases handled.** Missing scheme / `www` / fragments in URLs; duplicates; name-only leads; DNS failures,
timeouts, 404s, 429 with Retry-After, 5xx (bounded retries); CAPTCHA/bot-check pages (reported, never
bypassed); redirects to private addresses or other domains; non-HTML responses; oversized pages (cut at
`MAX_BYTES` and flagged with a warning so the reviewer knows only part was read);
JavaScript-only pages; missing About/Services pages; mixed residential + commercial businesses.

**URL safety (SSRF).** Only `http(s)` URLs are fetched; the host is resolved and refused if any address is
private, loopback, link-local or otherwise non-public, and the check is repeated on every redirect hop.
Known limitation: `requests` resolves the name again when it connects, so a DNS-rebinding attacker who
controls a domain could in theory swap in a private address between the check and the connection. Closing
that fully means pinning the connection to the checked IP (a custom transport adapter) or fetching through
an egress proxy that blocks private ranges, which is the production fix.

**The B2B rule (assumption).** The assignment does not define the ICP, so the default treats a lead as B2B when
its site shows it serves businesses or organisations; it is editable in the sidebar or with
`ICP_DESCRIPTION` in `.env`.

**With more time.** Playwright fallback for JavaScript sites; a paid search API plus Google Business Profile
for more reliable website discovery; concurrent crawling with per-domain politeness and robots.txt; semantic
(embedding) matching for paraphrased evidence; a small labelled set of leads to measure B2B accuracy across
prompt versions; authentication and PostgreSQL; a REST API and HubSpot/Salesforce sync.

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
