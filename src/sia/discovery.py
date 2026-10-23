"""Suggest an official website for name-only leads. Suggestions always need human confirmation."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .ingestion import normalize_url
from .models import Lead

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


def suggest_websites(lead: Lead, search=None, max_results: int = 8) -> list[dict]:
    """Return ranked candidate sites: [{url, title, score, reasons}]. Empty list if search is unavailable."""
    query = " ".join(filter(None, [lead.company_name, lead.descriptor, lead.location]))
    try:
        if search is None:
            from ddgs import DDGS
            results = DDGS().text(query, max_results=max_results)
        else:
            results = search(query, max_results)
    except Exception:  # search is best-effort; manual entry remains available
        return []
    candidates: dict[str, dict] = {}
    for result in results or []:
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
