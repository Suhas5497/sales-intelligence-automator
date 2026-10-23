"""Bounded same-site crawl: homepage plus the few pages most useful for a sales brief."""
from __future__ import annotations

from urllib.parse import urlparse

from .extractor import extract_text, page_title, pick_links, remove_repeated_lines
from .fetcher import Fetcher, FetchError
from .models import Page


def crawl(start_url: str, fetcher: Fetcher, max_pages: int = 4, max_chars: int = 6000) -> list[Page]:
    """Fetch the homepage (errors propagate) and up to max_pages-1 linked pages (errors recorded)."""
    home = fetcher.get(start_url)
    pages = [(home, *extract_text(home.html, home.final_url))]
    for link in pick_links(home.html, home.final_url, limit=max_pages - 1):
        try:
            resp = fetcher.get(link)
        except FetchError as exc:
            pages.append((None, link, exc.code))
            continue
        if urlparse(resp.final_url).hostname != urlparse(home.final_url).hostname:
            continue                                      # redirected off-site: ignore
        pages.append((resp, *extract_text(resp.html, resp.final_url)))

    fetched = [(r, t, m) for r, t, m in pages if r is not None]
    deduped = remove_repeated_lines([text.splitlines() for _, text, _ in fetched])
    result = []
    for (resp, _, method), lines in zip(fetched, deduped):
        result.append(Page(url=resp.url, final_url=resp.final_url, status_code=resp.status_code,
                           title=page_title(resp.html), text="\n".join(lines)[:max_chars], method=method))
    for resp, link, code in pages:
        if resp is None:
            result.append(Page(url=link, error=code))
    return result
