"""Web search for name-only leads: suggest an official website (always confirmed by a person) and collect
search snippets that clearly describe the company, for a preliminary brief."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .ingestion import normalize_url
from .models import Lead, Page

DIRECTORY_HOSTS = re.compile(
    r"(yelp|facebook|instagram|linkedin|twitter|x\.com|youtube|bbb\.org|yellowpages|angi|homeadvisor|thumbtack|"
    r"houzz|nextdoor|mapquest|manta|bizapedia|chamberofcommerce|google|apple\.com|tripadvisor|indeed|glassdoor|"
    r"wikipedia|porch|buildzoom|superpages|dnb\.com|zoominfo|opencorporates|birdeye|expertise\.com|reddit|"
    r"merchantcircle|cylex|hotfrog|brownbook|alignable|bark\.com|networx|threebestrated|\.gov$)", re.I)
MIN_SCORE = 0.4                                  # weaker matches are not worth showing
STOPWORDS = {"the", "and", "of", "inc", "llc", "co", "services", "service", "company", "&"}


def name_tokens(name: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", name.lower().replace("'", "")) if t not in STOPWORDS and len(t) > 1}


def score_candidate(lead: Lead, result: dict) -> tuple[float, list[str]]:
    """0..1 score from name tokens in domain/title and location in the snippet; reasons explain it."""
    href, title, body = result.get("href", ""), result.get("title", ""), result.get("body", "")
    host = (urlparse(href).hostname or "").removeprefix("www.")
    tokens = name_tokens(lead.company_name)
    if not tokens:
        return 0.0, []
    domain_hits = sum(t in host.replace("-", "") for t in tokens) / len(tokens)
    title_hits = sum(t in title.lower() for t in tokens) / len(tokens)
    city = lead.location.split(",")[0].strip().lower()
    city_hit = bool(city) and city in f"{title} {body}".lower()
    reasons = [f"name in domain {domain_hits:.0%}", f"name in title {title_hits:.0%}"]
    if city_hit:
        reasons.append(f"mentions {city.title()}")
    return round(0.45 * domain_hits + 0.35 * title_hits + 0.2 * city_hit, 2), reasons


def web_search(lead: Lead, search=None, max_results: int = 8) -> list[dict]:
    """Raw results [{href, title, body}] for the lead's name, description and location; [] on any failure."""
    query = " ".join(filter(None, [lead.company_name, lead.descriptor, lead.location]))
    try:
        if search is None:
            from ddgs import DDGS
            return list(DDGS().text(query, max_results=max_results) or [])
        return list(search(query, max_results) or [])
    except Exception:  # search is best-effort; manual entry remains available
        return []


def suggest_websites(lead: Lead, search=None, max_results: int = 8, results: list[dict] | None = None) -> list[dict]:
    """Return ranked candidate sites: [{url, title, score, reasons}]. Empty list if search is unavailable."""
    if results is None:
        results = web_search(lead, search, max_results)
    candidates: dict[str, dict] = {}
    for result in results:
        url = normalize_url(result.get("href", ""))
        host = urlparse(url).hostname or ""
        if not url or DIRECTORY_HOSTS.search(host):
            continue
        root = f"https://{host}/"
        score, reasons = score_candidate(lead, result)
        if score > candidates.get(root, {}).get("score", -1):
            candidates[root] = {"url": root, "title": result.get("title", "")[:120], "score": score, "reasons": reasons}
    ranked = [c for c in candidates.values() if c["score"] >= MIN_SCORE]
    return sorted(ranked, key=lambda c: -c["score"])[:3]


def snippet_pages(lead: Lead, results: list[dict], limit: int = 6) -> list[Page]:
    """Search snippets that are clearly about this company: most name words present and, when a location is
    known, the city mentioned. Directory listings (Yelp, BBB, ...) are allowed here because they describe the
    business; anything that might be a different company is left out."""
    tokens = name_tokens(lead.company_name)
    if not tokens:
        return []
    city = lead.location.split(",")[0].strip().lower()
    pages: dict[str, Page] = {}
    for result in results:
        href, title, body = result.get("href", ""), result.get("title", ""), result.get("body", "")
        text = f"{title}. {body}".strip(". ")
        lower = text.lower().replace("'", "")
        coverage = sum(t in lower for t in tokens) / len(tokens)
        if not href.startswith("http") or coverage < 0.75 or (city and city not in lower) or href in pages:
            continue
        pages[href] = Page(url=href, final_url=href, title=title[:200], text=text, method="search_snippet")
    return list(pages.values())[:limit]
