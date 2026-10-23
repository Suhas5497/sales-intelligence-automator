import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sia.fetcher import FetchError, Response  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeFetcher:
    """Maps URL -> fixture file name or FetchError code; records requested URLs."""

    def __init__(self, routes: dict[str, str]):
        self.routes = routes
        self.requested = []

    def get(self, url):
        self.requested.append(url)
        target = self.routes.get(url)
        if target is None:
            raise FetchError("HTTP_404", "not found")
        if target.isupper():
            raise FetchError(target, "simulated failure")
        return Response(url=url, final_url=url, status_code=200, html=fixture_html(target))


class FakeLLM:
    model = "fake-llm"

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def generate(self, system, prompt, schema):
        self.calls += 1
        out = self.outputs.pop(0)
        return out if isinstance(out, str) else json.dumps(out)


HOME = "https://www.lonestarroofing.com/"
COMMERCIAL = "https://www.lonestarroofing.com/commercial-roofing/"


def good_draft(**overrides):
    draft = {
        "company_overview": {"text": "Houston roofing contractor serving homes and businesses.",
                             "evidence": [{"source_url": HOME, "quote": "installs, repairs and replaces roofs for homeowners and businesses across the Greater Houston area"}]},
        "core_offering": {"text": "Roof installation, repair and replacement; commercial TPO, metal and flat roofs.",
                          "evidence": [{"source_url": HOME, "quote": "TPO, metal and flat roof systems"}]},
        "target_audience": {"text": "Homeowners plus property managers, general contractors and retail centers.",
                            "evidence": [{"source_url": HOME, "quote": "works with property managers, general contractors and retail centers"}]},
        "b2b_qualification": {"decision": "Yes", "rationale": "Explicit commercial team serving property managers.",
                              "evidence": [{"source_url": COMMERCIAL, "quote": "under annual maintenance agreements"}]},
        "discovery_questions": [
            {"question": "How many of your commercial roofs are currently under annual maintenance agreements?",
             "based_on": {"source_url": COMMERCIAL, "quote": "under annual maintenance agreements"}},
            {"question": "How do you currently generate leads from property managers and general contractors?",
             "based_on": {"source_url": HOME, "quote": "works with property managers, general contractors"}},
            {"question": "After a hail storm, how quickly can your team turn around free storm damage inspections?",
             "based_on": {"source_url": HOME, "quote": "free storm damage inspections"}},
        ],
    }
    draft.update(overrides)
    return draft


@pytest.fixture
def site_fetcher():
    return FakeFetcher({HOME: "home.html", COMMERCIAL: "commercial.html"})
