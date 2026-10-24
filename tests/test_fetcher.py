import pytest
import requests

from conftest import fixture_html
from sia.fetcher import Fetcher, FetchError
from sia.safety import UnsafeURL, check_url


class FakeResp:
    def __init__(self, status=200, html="", headers=None):
        self.status_code = status
        self.headers = {"Content-Type": "text/html", **(headers or {})}
        self.reason = "x"
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"
        self._body = html.encode()

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 307, 308)

    def iter_content(self, size):
        yield self._body

    def close(self):
        pass


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make(responses):
    return Fetcher(session=FakeSession(responses), sleep=lambda s: None)


def test_private_and_non_http_targets_are_refused():
    for url in ("http://127.0.0.1/", "http://localhost:8501/", "file:///etc/passwd", "http://169.254.169.254/latest"):
        with pytest.raises(UnsafeURL):
            check_url(url)


def test_redirect_to_private_address_is_blocked():
    fetcher = make([FakeResp(302, headers={"Location": "http://10.0.0.5/admin"})])
    with pytest.raises(FetchError) as err:
        fetcher.get("https://93.184.216.34/")
    assert err.value.code == "UNSAFE_URL"


def test_transient_errors_retry_then_succeed():
    fetcher = make([requests.exceptions.Timeout(), FakeResp(503), FakeResp(200, "<p>ok</p>")])
    assert fetcher.get("https://93.184.216.34/").html == "<p>ok</p>"


def test_404_is_not_retried_and_challenge_is_reported():
    fetcher = make([FakeResp(404)])
    with pytest.raises(FetchError) as err:
        fetcher.get("https://93.184.216.34/")
    assert err.value.code == "HTTP_404" and len(fetcher.session.calls) == 1
    fetcher = make([FakeResp(200, fixture_html("challenge.html"))])
    with pytest.raises(FetchError) as err:
        fetcher.get("https://93.184.216.34/")
    assert err.value.code == "BLOCKED_BY_CHALLENGE"


def test_oversized_page_is_cut_and_flagged():
    fetcher = Fetcher(session=FakeSession([FakeResp(200, "<p>" + "a" * 500 + "</p>")]), sleep=lambda s: None,
                      max_bytes=100)
    resp = fetcher.get("https://93.184.216.34/")
    assert resp.truncated and len(resp.html) == 100


def test_normal_page_is_not_flagged():
    assert not make([FakeResp(200, "<p>ok</p>")]).get("https://93.184.216.34/").truncated
