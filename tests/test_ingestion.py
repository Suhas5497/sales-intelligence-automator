from sia.ingestion import deduplicate, normalize_url, parse_csv, parse_line, parse_text
from pathlib import Path

SAMPLE = Path(__file__).resolve().parents[1] / "sample_data"


def test_url_lead_is_normalised():
    lead = parse_line("https://www.RedTruckBakery.com#top")
    assert lead.website == "https://www.redtruckbakery.com/"
    assert lead.website_source == "input"


def test_name_descriptor_and_location_are_split():
    lead = parse_line("BrightPlay Turf – Artificial Turf & Landscaping, Chicago IL")
    assert (lead.company_name, lead.descriptor, lead.location) == ("BrightPlay Turf", "Artificial Turf & Landscaping", "Chicago, IL")
    lead = parse_line("Joe's Backyard Landscaping – Phoenix AZ")
    assert (lead.company_name, lead.location, lead.website) == ("Joe's Backyard Landscaping", "Phoenix, AZ", "")


def test_all_assignment_leads_parse():
    leads = parse_text((SAMPLE / "sample_leads.txt").read_text(encoding="utf-8"))
    assert len(leads) == 15
    assert sum(bool(l.website) for l in leads) == 7
    assert all(l.company_name and l.location for l in leads if not l.website)


def test_bad_urls_and_duplicates():
    assert normalize_url("ftp://example.com") == ""
    assert normalize_url("not a url") == ""
    unique, dupes = deduplicate(parse_text("example.com\nhttps://www.example.com/\nAcme – Dallas TX\nACME – Dallas TX"))
    assert len(unique) == 2 and len(dupes) == 2


def test_csv_with_headers_and_free_text():
    leads = parse_csv((SAMPLE / "sample_leads.csv").read_bytes())
    assert [bool(l.website) for l in leads] == [True, False, True, False]
    assert leads[2].website == "https://redtruckbakery.com/"
    leads = parse_csv("Lead\nBlue Ridge HVAC Services – Roanoke VA\n")
    assert leads[0].location == "Roanoke, VA"
