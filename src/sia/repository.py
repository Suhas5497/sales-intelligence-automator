"""SQLite persistence. Every run is a new brief row (history kept); reviewer edits live in their own
columns so the generated draft is never overwritten."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .llm import PROMPT_VERSION
from .models import Lead, LeadResult, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    lead_key TEXT UNIQUE NOT NULL,
    raw_input TEXT, company_name TEXT, location TEXT, descriptor TEXT,
    website TEXT, website_source TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS briefs (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    created_at TEXT, status TEXT, model TEXT, prompt_version TEXT,
    error_code TEXT, error_message TEXT,
    pages_json TEXT, draft_json TEXT, issues_json TEXT, candidates_json TEXT,
    review_status TEXT, reviewed_json TEXT, reviewer TEXT, review_notes TEXT, reviewed_at TEXT
);
"""


class Repository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def upsert_lead(self, lead: Lead, lead_id: int | None = None) -> int:
        fields = (lead.key, lead.raw_input, lead.company_name, lead.location, lead.descriptor,
                  lead.website, lead.website_source)
        with self.conn:
            if lead_id is None:
                row = self.conn.execute("SELECT id FROM leads WHERE lead_key = ?", (lead.key,)).fetchone()
                lead_id = row["id"] if row else None
            if lead_id is None:
                cur = self.conn.execute(
                    "INSERT INTO leads (lead_key, raw_input, company_name, location, descriptor, website, "
                    "website_source, created_at) VALUES (?,?,?,?,?,?,?,?)", (*fields, utcnow()))
                return cur.lastrowid
            clash = self.conn.execute("SELECT id FROM leads WHERE lead_key = ? AND id != ?",
                                      (lead.key, lead_id)).fetchone()
            if clash:                       # the confirmed website already belongs to a saved lead: merge them
                self.conn.execute("UPDATE briefs SET lead_id = ? WHERE lead_id = ?", (clash["id"], lead_id))
                self.conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
                lead_id = clash["id"]
            self.conn.execute(
                "UPDATE leads SET lead_key=?, raw_input=?, company_name=?, location=?, descriptor=?, website=?, "
                "website_source=? WHERE id=?", (*fields, lead_id))
            return lead_id

    def save_result(self, result: LeadResult, lead_id: int | None = None) -> int:
        lead_id = self.upsert_lead(result.lead, lead_id)
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO briefs (lead_id, created_at, status, model, prompt_version, error_code, error_message, "
                "pages_json, draft_json, issues_json, candidates_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (lead_id, result.created_at, result.status.value, result.model, PROMPT_VERSION,
                 result.error_code, result.error_message,
                 json.dumps([p.model_dump() for p in result.pages]),
                 result.draft.model_dump_json() if result.draft else None,
                 json.dumps([i.model_dump() for i in result.issues]),
                 json.dumps(result.candidates)))
            return cur.lastrowid

    def save_review(self, brief_id: int, decision: str, edited: dict | None, reviewer: str = "", notes: str = ""):
        with self.conn:
            self.conn.execute(
                "UPDATE briefs SET review_status=?, reviewed_json=?, reviewer=?, review_notes=?, reviewed_at=? "
                "WHERE id=?", (decision, json.dumps(edited) if edited else None, reviewer, notes, utcnow(), brief_id))

    def latest(self) -> list[dict]:
        """Latest brief per lead, newest leads last, with JSON columns decoded."""
        rows = self.conn.execute(
            "SELECT l.id AS lead_id, l.raw_input, l.company_name, l.location, l.descriptor, l.website, "
            "l.website_source, b.* FROM leads l JOIN briefs b ON b.id = "
            "(SELECT MAX(id) FROM briefs WHERE lead_id = l.id) ORDER BY l.id").fetchall()
        return [decode(dict(row)) for row in rows]

    def clear(self):
        with self.conn:
            self.conn.execute("DELETE FROM briefs")
            self.conn.execute("DELETE FROM leads")


def decode(row: dict) -> dict:
    for key in ("pages_json", "draft_json", "issues_json", "candidates_json", "reviewed_json"):
        value = row.get(key)
        row[key.removesuffix("_json")] = json.loads(value) if value else None
        row.pop(key, None)
    row["lead_id"] = row.get("lead_id")
    row["brief_id"] = row.pop("id")
    row["final"] = row["reviewed"] or row["draft"]
    row["effective_status"] = row["review_status"] or row["status"]
    return row
