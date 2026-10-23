from conftest import COMMERCIAL, HOME, fixture_html

from sia.crawler import crawl
from sia.extractor import extract_text, pick_links


def test_boilerplate_is_removed():
    text, _ = extract_text(fixture_html("home.html"), HOME)
    assert "commercial team works with property managers" in text
    for junk in ("We use cookies", "Accept all", "All rights reserved", "Call now"):
        assert junk not in text


def test_link_picker_prefers_useful_same_site_pages():
    links = pick_links(fixture_html("home.html"), HOME, limit=3)
    assert COMMERCIAL in links
    assert all("lonestarroofing.com" in l for l in links)
    assert not any("privacy" in l or "blog" in l or "facebook" in l for l in links)


def test_crawl_dedupes_repeated_text_and_records_failed_pages(site_fetcher):
    pages = crawl(HOME, site_fetcher, max_pages=4)
    texts = {p.url: p.text for p in pages}
    assert "homeowners and businesses" in texts[HOME]
    assert "homeowners and businesses" not in texts[COMMERCIAL]       # repeated block dropped
    assert "annual maintenance agreements" in texts[COMMERCIAL]
    assert any(p.error == "HTTP_404" for p in pages)                    # /about-us/ is missing


def test_js_shell_yields_little_text():
    text, _ = extract_text(fixture_html("js_shell.html"))
    assert len(text) < 50
