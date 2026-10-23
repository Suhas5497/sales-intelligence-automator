# Reference notes

Patterns were taken from an earlier project of mine, **Amber Property QC Automation** (Playwright + local
Ollama, compares property listings with provider websites). No files were copied; these ideas were
re-implemented in smaller form:

| Amber component | Idea reused here | Changes |
|---|---|---|
| `errors.py` (ACCESS / TRANSIENT / TECHNICAL error codes) | Stable failure codes; only transient errors (timeouts, 429, 5xx) are retried | Simplified to `FetchError(code)` in `fetcher.py` |
| `llm_extractor.py` `_accept()` (value kept only if its evidence is verbatim page text) | Every LLM claim needs a quote that is found in the scraped text, else it is dropped | Applied to brief fields, B2B evidence and questions in `validation.py` |
| `extraction.py` `detect_access_challenge()` | CAPTCHA / "just a moment" pages are reported, never bypassed | Works on raw HTML instead of a Playwright page |
| `ingestion.py` header canonicalisation and URL validation | Case/space-insensitive CSV headers, http(s)-only URLs | Added name/location parsing and duplicate detection |
| Per-job isolation in `pipeline.py` / `job_runner.py` | One failing lead never stops the batch | In-process try/except instead of one process per job |
| Statuses Correct / Needs review / Error and the review sheet | Human review is a first-class status, edits stored separately | Streamlit review tab and SQLite columns |

Not reused: Playwright rendering, site-specific provider adapters, Excel/PDF reporting and local Ollama, because
this assignment needs breadth across many small static sites rather than deep extraction from a few complex ones.
