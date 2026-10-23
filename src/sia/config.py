"""Settings read from environment variables (and .env when present)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_ICP = (
    "We sell to businesses (B2B). A lead is a relevant B2B lead when its website shows it serves other "
    "businesses or organisations: commercial clients, property managers, contractors, offices, restaurants, "
    "wholesale/trade accounts, corporate or institutional customers. A company that only serves individual "
    "homeowners or retail consumers is not a B2B lead."
)


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))
    db_path: Path = field(default_factory=lambda: Path(os.getenv("DB_PATH", "data/briefs.sqlite3")))
    max_pages: int = field(default_factory=lambda: _int("MAX_PAGES_PER_LEAD", 4))
    request_timeout: int = field(default_factory=lambda: _int("REQUEST_TIMEOUT_SECONDS", 15))
    max_retries: int = field(default_factory=lambda: _int("MAX_RETRIES", 2))
    max_bytes: int = field(default_factory=lambda: _int("MAX_RESPONSE_BYTES", 2_000_000))
    max_chars_per_page: int = field(default_factory=lambda: _int("MAX_CHARS_PER_PAGE", 6000))
    min_useful_chars: int = field(default_factory=lambda: _int("MIN_USEFUL_CHARS", 300))
    icp: str = field(default_factory=lambda: os.getenv("ICP_DESCRIPTION", DEFAULT_ICP))
