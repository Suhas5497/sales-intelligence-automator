"""Main-text extraction and boilerplate removal (cookie banners, menus, footers, repeated blocks)."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import trafilatura
from bs4 import BeautifulSoup

BOILERPLATE_ATTR = re.compile(
    r"(?:^|[\s_-])(cookies?|consent|gdpr|banner|popup|modal|newsletter|subscribe|nav|navbar|navigation|menu-main|"
    r"main-menu|site-header|site-footer|footer|breadcrumbs?|social|share|sidebar|widget|skip-link|chat)(?:$|[\s_-])", re.I)
BOILERPLATE_LINE = re.compile(
    r"^(accept( all)?( cookies)?|reject|we use cookies.*|cookie (settings|policy).*|privacy policy|terms( of (use|service))?|"
    r"all rights reserved.*|©.*|copyright.*|skip to (main )?content|menu|home|call now|get a (free )?quote|"
    r"book now|contact us|learn more|read more|follow us.*|powered by .*|sitemap)$", re.I)
# Page types worth reading for a sales brief, best first.
LINK_PRIORITIES = [
    (re.compile(r"about|who-we-are|our-story|company", re.I), 3),
    (re.compile(r"service|product|solution|what-we-do|offer|menu|catering|wholesale", re.I), 3),
    (re.compile(r"commercial|business|industr|client|partner|trade|corporate|b2b|property-manag", re.I), 4),
    (re.compile(r"residential|home|gallery|project|portfolio", re.I), 1),
]
SKIP_LINK = re.compile(r"\.(pdf|jpe?g|png|gif|zip|docx?)$|login|cart|checkout|account|privacy|terms|cookie|"
                       r"blog/|/tag/|/category/|wp-admin|mailto:|tel:|javascript:|careers|jobs", re.I)


def page_title(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    return soup.title.get_text(" ", strip=True)[:200] if soup.title else ""


def meta_description(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)}) or \
        soup.find("meta", attrs={"property": "og:description"})
    return (tag.get("content") or "").strip() if tag else ""


def _fallback_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form", "nav", "header", "footer", "aside"]):
        tag.decompose()
    for tag in soup.find_all(True):
        if tag.attrs is None:
            continue
        attrs = " ".join([tag.get("id") or ""] + list(tag.get("class") or []))
        if attrs and BOILERPLATE_ATTR.search(attrs) and tag.name not in ("body", "main", "html"):
            tag.decompose()
    return soup.get_text("\n", strip=True)


def clean_lines(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) < 3 or BOILERPLATE_LINE.match(line):
            continue
        lines.append(line)
    return lines


def extract_text(html: str, url: str = "") -> tuple[str, str]:
    """Return (text, method). Trafilatura first; BeautifulSoup cleanup when it finds too little."""
    text = trafilatura.extract(html, url=url or None, include_comments=False, include_tables=True,
                               favor_recall=True, deduplicate=True) or ""
    method = "trafilatura"
    if len(text) < 200:
        fallback = _fallback_text(html)
        if len(fallback) > len(text):
            text, method = fallback, "bs4_fallback"
    description = meta_description(html)
    lines = clean_lines(text)
    if description and description not in text:
        lines.insert(0, description)
    return "\n".join(lines), method


def remove_repeated_lines(pages_lines: list[list[str]]) -> list[list[str]]:
    """Drop lines already seen on an earlier page of the same site (repeated marketing blocks)."""
    seen: set[str] = set()
    result = []
    for lines in pages_lines:
        kept = [line for line in lines if line.casefold() not in seen]
        seen.update(line.casefold() for line in lines)
        result.append(kept)
    return result


def _same_site(url: str, root_host: str) -> bool:
    host = (urlparse(url).hostname or "").removeprefix("www.")
    return host == root_host.removeprefix("www.")


def pick_links(html: str, base_url: str, limit: int = 3) -> list[str]:
    """Choose the most useful same-site pages (About, Services, Commercial ...) to read next."""
    soup = BeautifulSoup(html, "lxml")
    root_host = urlparse(base_url).hostname or ""
    scored: dict[str, int] = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"]).split("#")[0]
        if not href.startswith("http") or not _same_site(href, root_host) or SKIP_LINK.search(href):
            continue
        if href.rstrip("/") == base_url.rstrip("/"):
            continue
        label = f"{urlparse(href).path} {a.get_text(' ', strip=True)}"
        score = sum(weight for pattern, weight in LINK_PRIORITIES if pattern.search(label))
        if score:
            score -= urlparse(href).path.count("/") // 3          # prefer shallow pages
            scored[href] = max(score, scored.get(href, 0))
    return [url for url, _ in sorted(scored.items(), key=lambda item: -item[1])][:limit]
