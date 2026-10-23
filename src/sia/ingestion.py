"""Turn pasted text or CSV rows into normalised, de-duplicated Lead records."""
from __future__ import annotations

import csv
import io
import re
from urllib.parse import urlparse

from .models import Lead

URL_RE = re.compile(r"^(https?://)?([a-z0-9-]+\.)+[a-z]{2,}([/?#]\S*)?$", re.I)
# "Phoenix AZ" / "Portland, OR" at the end of a string
LOCATION_RE = re.compile(r"(?:^|,\s*|\s)([A-Z][A-Za-z .'-]+?),?\s+([A-Z]{2})$")
SEPARATORS = re.compile(r"\s+[–—-]\s+")     # en dash, em dash or spaced hyphen
HEADER_ALIASES = {
    "company": "company_name", "company_name": "company_name", "name": "company_name", "business": "company_name",
    "website": "website", "url": "website", "website_url": "website", "domain": "website", "site": "website",
    "location": "location", "city": "location", "lead": "lead", "input": "lead",
}


def normalize_url(value: str) -> str:
    """Return https://host/path without fragments or tracking query, or "" if not a usable web URL."""
    value = (value or "").strip().strip("<>\"'")
    if not value:
        return ""
    if not re.match(r"^[a-z][a-z0-9+.-]*://", value, re.I):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or "." not in parsed.hostname:
        return ""
    host = parsed.hostname.lower()
    path = parsed.path if parsed.path not in ("", "/") else "/"
    return f"{parsed.scheme}://{host}{path}"


# RFC 2606 / 6761 names that never exist on the public internet
RESERVED_TLDS = (".example", ".test", ".invalid", ".localhost", ".local")


def is_reserved_host(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host.endswith(RESERVED_TLDS) or host in ("example.com", "example.org", "example.net")


def looks_like_url(text: str) -> bool:
    return bool(URL_RE.match(text.strip()))


def parse_line(line: str) -> Lead | None:
    """Parse one free-text lead: a URL, or 'Name – descriptor, City ST', or just a name."""
    raw = line.strip().strip(",")
    if not raw or raw.startswith("#"):
        return None
    if looks_like_url(raw):
        return Lead(raw_input=raw, website=normalize_url(raw), website_source="input")
    parts = SEPARATORS.split(raw, maxsplit=1)
    name, rest = parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")
    location, descriptor = "", rest
    match = LOCATION_RE.search(rest) if rest else LOCATION_RE.search(name)
    if match:
        location = f"{match.group(1).strip()}, {match.group(2)}"
        if rest:
            descriptor = rest[: match.start()].strip(" ,")
        else:
            name = name[: match.start()].strip(" ,")
    return Lead(raw_input=raw, company_name=name, location=location, descriptor=descriptor)


def parse_text(text: str) -> list[Lead]:
    return [lead for lead in (parse_line(line) for line in text.splitlines()) if lead]


def _canonical(header: str) -> str:
    return HEADER_ALIASES.get(re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_"), "")


def parse_csv(content: str | bytes) -> list[Lead]:
    """CSV with any of: company_name, website, location — or a single 'lead' column of free text."""
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(content))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return []
    columns = [_canonical(h) for h in rows[0]]
    if not any(columns):                      # no header: treat every first cell as a free-text lead
        return [lead for lead in (parse_line(row[0]) for row in rows) if lead]
    leads = []
    for row in rows[1:]:
        record = {col: (row[i].strip() if i < len(row) else "") for i, col in enumerate(columns) if col}
        if record.get("lead"):
            lead = parse_line(record["lead"])
        elif record.get("company_name") or record.get("website"):
            website = normalize_url(record.get("website", ""))
            lead = Lead(raw_input=",".join(row), company_name=record.get("company_name", ""),
                        location=record.get("location", ""), website=website,
                        website_source="input" if website else "")
        else:
            lead = None
        if lead:
            leads.append(lead)
    return leads


def deduplicate(leads: list[Lead]) -> tuple[list[Lead], list[Lead]]:
    """Return (unique, duplicates) keyed by host for URL leads and name+location otherwise."""
    seen, unique, dupes = set(), [], []
    for lead in leads:
        if lead.key in seen:
            dupes.append(lead)
        else:
            seen.add(lead.key)
            unique.append(lead)
    return unique, dupes
