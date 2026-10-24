"""HTTP retrieval with timeouts, bounded retries, size limits and redirect safety checks.

Failure codes follow the categories used in the Amber QC project:
ACCESS (blocked / not found, never retried), TRANSIENT (retried with backoff), TECHNICAL.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin

import requests

from .safety import UnsafeURL, check_url

# Many small-business hosts answer 403 to non-browser user agents, so a normal browser UA is sent.
# Access controls (CAPTCHA, bot checks) are still detected and never bypassed.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36")
CHALLENGE_PHRASES = ("verify you are human", "checking your browser", "are you a robot",
                     "enable javascript and cookies to continue", "performing security verification")
CHALLENGE_TITLES = re.compile(r"^(just a moment|attention required|access denied|security check)", re.I)
MAX_REDIRECTS = 5


class FetchError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass
class Response:
    url: str            # requested URL
    final_url: str      # after redirects
    status_code: int
    html: str
    truncated: bool = False  # body was cut at max_bytes


def detect_challenge(html: str) -> bool:
    lower = html[:20000].casefold()
    title = re.search(r"<title[^>]*>(.*?)</title>", lower, re.S)
    if title and CHALLENGE_TITLES.search(title.group(1).strip()):
        return True
    return any(phrase in lower for phrase in CHALLENGE_PHRASES)


class Fetcher:
    def __init__(self, timeout: int = 15, max_retries: int = 2, max_bytes: int = 2_000_000,
                 session: requests.Session | None = None, sleep=time.sleep):
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_bytes = max_bytes
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9",
                                     "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
        self.sleep = sleep

    def get(self, url: str) -> Response:
        attempt = 0
        while True:
            try:
                return self._get_once(url)
            except FetchError as exc:
                transient = exc.code in {"TIMEOUT", "CONNECTION_ERROR", "HTTP_429"} or exc.code.startswith("HTTP_5")
                if not transient or attempt >= self.max_retries:
                    raise
                attempt += 1
                retry_after = getattr(exc, "retry_after", None)
                self.sleep(min(retry_after, 10) if retry_after else 2 ** attempt)

    def _get_once(self, url: str) -> Response:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            try:
                check_url(current)
            except UnsafeURL as exc:
                raise FetchError("UNSAFE_URL", str(exc)) from exc
            try:
                resp = self.session.get(current, timeout=self.timeout, allow_redirects=False, stream=True)
            except requests.exceptions.Timeout as exc:
                raise FetchError("TIMEOUT", f"no response within {self.timeout}s") from exc
            except requests.exceptions.SSLError as exc:
                raise FetchError("SSL_ERROR", str(exc)[:200]) from exc
            except requests.exceptions.ConnectionError as exc:
                text = str(exc)
                if "Name or service not known" in text or "getaddrinfo failed" in text or "NameResolution" in text:
                    raise FetchError("DNS_NOT_RESOLVED", "domain does not resolve") from exc
                raise FetchError("CONNECTION_ERROR", text[:200]) from exc
            except requests.exceptions.RequestException as exc:
                raise FetchError("REQUEST_FAILED", str(exc)[:200]) from exc
            if resp.is_redirect and resp.headers.get("Location"):
                current = urljoin(current, resp.headers["Location"])
                resp.close()
                continue
            return self._read(url, current, resp)
        raise FetchError("TOO_MANY_REDIRECTS", f"more than {MAX_REDIRECTS} redirects")

    def _read(self, url: str, final_url: str, resp: requests.Response) -> Response:
        if resp.status_code == 429:
            error = FetchError("HTTP_429", "rate limited by the website")
            try:
                error.retry_after = int(resp.headers.get("Retry-After", ""))
            except ValueError:
                pass
            raise error
        if resp.status_code >= 400:
            raise FetchError(f"HTTP_{resp.status_code}", f"server answered {resp.status_code} {resp.reason}")
        ctype = resp.headers.get("Content-Type", "")
        if ctype and "html" not in ctype and "xml" not in ctype:
            raise FetchError("NOT_HTML", f"content type is {ctype}")
        body, truncated = b"", False
        for chunk in resp.iter_content(65536):
            body += chunk
            if len(body) > self.max_bytes:
                body, truncated = body[:self.max_bytes], True   # keep the first max_bytes and say so
                resp.close()
                break
        html = body.decode(resp.encoding or resp.apparent_encoding or "utf-8", errors="replace")
        if detect_challenge(html):
            raise FetchError("BLOCKED_BY_CHALLENGE", "site shows a CAPTCHA / bot check; not bypassed")
        return Response(url=url, final_url=final_url, status_code=resp.status_code, html=html,
                        truncated=truncated)
