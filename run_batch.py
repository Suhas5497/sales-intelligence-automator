"""Headless run: python run_batch.py sample_data/sample_leads.txt [--csv out.csv] [--json out.json] [--hubspot out.csv]"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from sia.config import Settings  # noqa: E402
from sia.export import to_csv, to_hubspot_csv, to_json  # noqa: E402
from sia.fetcher import Fetcher  # noqa: E402
from sia.ingestion import deduplicate, parse_csv, parse_text  # noqa: E402
from sia.llm import GeminiClient, LLMError  # noqa: E402
from sia.pipeline import process_batch  # noqa: E402
from sia.repository import Repository  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help=".txt (one lead per line) or .csv")
    parser.add_argument("--csv", default="output/sales_briefs.csv")
    parser.add_argument("--json", default="output/sales_briefs.json")
    parser.add_argument("--hubspot", default="output/hubspot_companies.csv", help="CRM import file")
    parser.add_argument("--no-search", action="store_true", help="do not suggest websites for name-only leads")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("trafilatura").setLevel(logging.ERROR)

    settings = Settings()
    path = Path(args.input)
    leads = parse_csv(path.read_bytes()) if path.suffix.lower() == ".csv" else parse_text(path.read_text("utf-8"))
    leads, dupes = deduplicate(leads)
    try:
        llm = GeminiClient(settings.gemini_api_key, settings.gemini_model)
    except LLMError as exc:
        print(f"warning: {exc.message}; briefs will not be generated")
        llm = None
    repo = Repository(settings.db_path)

    def report(i, total, result):
        repo.save_result(result)
        print(f"[{i}/{total}] {result.lead.display_name:<45} {result.status.value:<22} {result.error_code}")

    fetcher = Fetcher(settings.request_timeout, settings.max_retries, settings.max_bytes)
    process_batch(leads, fetcher, llm, settings, on_result=report, use_search=not args.no_search)
    rows = repo.latest()
    for out, content in ((args.csv, to_csv(rows)), (args.json, to_json(rows)), (args.hubspot, to_hubspot_csv(rows))):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(content, encoding="utf-8")
    print(f"saved {len(rows)} briefs to {settings.db_path}, {args.csv}, {args.json}, {args.hubspot}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
