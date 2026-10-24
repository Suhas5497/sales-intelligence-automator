"""CSV / JSON exports of the latest brief per lead (reviewer edits win over the generated draft), plus a
CRM import file laid out as HubSpot company properties."""
from __future__ import annotations

import csv
import io
import json
import re
from urllib.parse import urlparse


def company_name(row: dict) -> str:
    """Name from the input, else the homepage title segment that best matches the domain
    ("Houston Roofing | Expert Roof Replacement" on houstonroofingonline.com -> "Houston Roofing")."""
    if row.get("company_name"):
        return row["company_name"]
    pages = row.get("pages") or []
    host = (urlparse(row.get("website") or "").hostname or "").removeprefix("www.")
    segments = [s.strip() for s in re.split(r"\s[|–—-]\s|\|", pages[0].get("title", ""))] if pages else []
    segments = [s for s in segments if s and s.lower() not in {"home", "welcome", "homepage"}]
    if not segments:
        return host or row.get("raw_input", "")
    def overlap(segment):
        return sum(w in host for w in re.findall(r"[a-z]{3,}", segment.lower()))
    return max(segments, key=overlap)


def flatten(row: dict) -> dict:
    brief = row.get("final") or {}
    def claim(name):
        return (brief.get(name) or {}).get("text", "")
    b2b = brief.get("b2b_qualification") or {}
    questions = [q.get("question", "") for q in brief.get("discovery_questions") or []] + ["", "", ""]
    sources = sorted({ev["source_url"] for part in brief.values() if isinstance(part, dict)
                      for ev in part.get("evidence", []) if isinstance(ev, dict) and ev.get("source_url")})
    return {
        "company": company_name(row),
        "location": row.get("location", ""),
        "website": row.get("website", ""),
        "status": row.get("effective_status", ""),
        "company_overview": claim("company_overview"),
        "core_offering": claim("core_offering"),
        "target_audience": claim("target_audience"),
        "b2b_decision": b2b.get("decision", ""),
        "b2b_rationale": b2b.get("rationale", ""),
        "question_1": questions[0], "question_2": questions[1], "question_3": questions[2],
        "evidence_urls": " ".join(sources),
        "validation_issues": " | ".join(f"{i['severity']}: {i['field']}: {i['message']}" for i in row.get("issues") or []),
        "error": " ".join(filter(None, [row.get("error_code"), row.get("error_message")])),
        "reviewer_notes": row.get("review_notes") or "",
        "generated_at": row.get("created_at", ""),
        "model": row.get("model", ""),
    }


# HubSpot's standard company properties first, then custom ones a CRM admin creates once (group "Sales brief").
HUBSPOT_COLUMNS = {
    "Company name": "company", "Company Domain Name": "domain", "City": "city", "State/Region": "state",
    "Description": "company_overview", "Core offering": "core_offering", "Target audience": "target_audience",
    "B2B fit": "b2b_decision", "B2B rationale": "b2b_rationale", "Discovery questions": "questions",
    "Brief status": "status", "Brief evidence URLs": "evidence_urls", "Brief generated at": "generated_at",
}


def to_hubspot_row(row: dict) -> dict:
    flat = flatten(row)
    city, _, state = (flat["location"] or "").partition(",")
    flat.update(domain=(urlparse(flat["website"]).hostname or "").removeprefix("www."), city=city.strip(),
                state=state.strip().split(",")[0].strip(),
                questions="\n".join(f"{i}. {q}" for i, q in enumerate(
                    filter(None, [flat["question_1"], flat["question_2"], flat["question_3"]]), 1)))
    return {column: flat[key] for column, key in HUBSPOT_COLUMNS.items()}


def to_hubspot_csv(rows: list[dict]) -> str:
    """Import file for HubSpot (Contacts > Companies > Import); the domain is the de-duplication key."""
    return _write([to_hubspot_row(r) for r in rows])


def to_csv(rows: list[dict]) -> str:
    return _write([flatten(r) for r in rows])


def _write(flat: list[dict]) -> str:
    if not flat:
        return ""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(flat[0]))
    writer.writeheader()
    writer.writerows(flat)
    return out.getvalue()


def to_json(rows: list[dict]) -> str:
    keep = ("lead_id", "brief_id", "raw_input", "company_name", "location", "website", "status", "review_status",
            "error_code", "error_message", "draft", "reviewed", "issues", "pages", "candidates", "model",
            "prompt_version", "created_at", "reviewer", "review_notes", "reviewed_at")
    return json.dumps([{k: r.get(k) for k in keep} for r in rows], indent=2)
