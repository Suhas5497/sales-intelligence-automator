"""Streamlit UI: streamlit run app.py

Workspace layout: sidebar navigation, status filters, lead list on the left, the selected brief on the right
with an evidence reader that highlights every verified quote inside the scraped page text.
"""
from __future__ import annotations

import html
import logging
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))

from sia.config import Settings  # noqa: E402
from sia.export import to_csv, to_json  # noqa: E402
from sia.fetcher import Fetcher  # noqa: E402
from sia.highlight import highlight  # noqa: E402
from sia.ingestion import deduplicate, normalize_url, parse_csv, parse_text  # noqa: E402
from sia.llm import GeminiClient, LLMError  # noqa: E402
from sia.models import Lead, Status  # noqa: E402
from sia.pipeline import process_lead  # noqa: E402
from sia.repository import Repository  # noqa: E402

SAMPLE = Path(__file__).parent / "sample_data" / "sample_leads.txt"
# status -> (css class, filter group)
STATUS_STYLE = {
    "Ready": ("ok", "Ready"), "Approved": ("ok", "Approved"), "Needs review": ("warn", "Needs review"),
    "Website needed": ("info", "Website needed"), "Fetch failed": ("bad", "Failed"),
    "Insufficient content": ("bad", "Failed"), "LLM failed": ("bad", "Failed"), "Error": ("bad", "Failed"),
    "Rejected": ("muted", "Rejected"),
}
FILTERS = ["Ready", "Needs review", "Website needed", "Failed", "Approved", "Rejected"]
NEEDS_ACTION = {"Needs review", "Website needed", "Failed"}
FIELDS = (("Overview", "company_overview"), ("Core service", "core_offering"), ("Target customer", "target_audience"))

CSS = """
<style>
.block-container {padding-top: 1.6rem; max-width: 1400px;}
.sia-badge {display:inline-block; padding:3px 10px; border-radius:999px; font-size:0.78rem; font-weight:600;}
.sia-ok {background:rgba(22,163,74,.16); color:#15803d;}
.sia-warn {background:rgba(217,119,6,.18); color:#b45309;}
.sia-info {background:rgba(37,99,235,.15); color:#1d4ed8;}
.sia-bad {background:rgba(220,38,38,.14); color:#b91c1c;}
.sia-muted {background:rgba(120,120,120,.18); color:inherit;}
.sia-card {border:1px solid rgba(128,128,128,.28); border-radius:12px; padding:14px 16px; height:100%;}
.sia-card h4 {margin:0 0 6px; font-size:0.78rem; text-transform:uppercase; letter-spacing:.04em; opacity:.7;}
.sia-card p {margin:0; font-size:0.93rem; line-height:1.5;}
.sia-meta {opacity:.72; font-size:0.88rem;}
.sia-alert {border-radius:10px; padding:10px 14px; margin:4px 0 10px; font-size:0.92rem;
            background:rgba(217,119,6,.14); border:1px solid rgba(217,119,6,.35);}
.sia-alert.bad {background:rgba(220,38,38,.10); border-color:rgba(220,38,38,.35);}
.sia-source {font-family: Georgia, 'Source Serif 4', serif; font-size:0.92rem; line-height:1.7;
             white-space:pre-wrap; border-radius:10px; padding:14px 16px; background:rgba(128,128,128,.08);
             max-height:420px; overflow-y:auto;}
.sia-source mark {background:rgba(251,146,60,.45); color:inherit; padding:0 2px; border-radius:3px;}
.sia-q {margin:0 0 8px; line-height:1.5;}
.sia-brief {border:1px solid rgba(128,128,128,.28); border-radius:12px; padding:4px 18px; margin:6px 0 14px;}
.sia-sec {display:block; padding:12px 0; border-bottom:1px solid rgba(128,128,128,.18);}
.sia-sec:last-child {border-bottom:none;}
.sia-lbl {display:block; font-size:0.72rem; font-weight:600; text-transform:uppercase; letter-spacing:.05em; opacity:.65; margin-bottom:3px;}
.sia-txt {display:block; line-height:1.55;}
/* blue accent instead of Streamlit's default red */
button[kind="primary"], [data-testid="stBaseButton-primary"] {background:#2563EB !important; border-color:#2563EB !important; color:#fff !important;}
button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover {background:#1D4ED8 !important; border-color:#1D4ED8 !important;}
[data-testid="stBaseButton-pillsActive"] {background:rgba(37,99,235,.15) !important; border-color:#2563EB !important; color:inherit !important;}
[data-testid="stSidebar"] [role="radiogroup"] label {padding:6px 0;}
.sia-leadbtn button {justify-content:flex-start; text-align:left;}
.sia-q small {display:block; opacity:.65; margin-top:2px;}
</style>
"""

logging.getLogger("trafilatura").setLevel(logging.ERROR)
st.set_page_config(page_title="Sales Intelligence Automator", page_icon="🔎", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)
try:  # badge text colours follow Streamlit's active theme
    if st.context.theme.type == "dark":
        st.markdown("<style>.sia-ok{color:#4ade80}.sia-warn{color:#fbbf24}.sia-info{color:#93c5fd}"
                    ".sia-bad{color:#fca5a5}</style>", unsafe_allow_html=True)
except AttributeError:
    pass
settings = Settings()


@st.cache_resource
def get_repo(path: str) -> Repository:
    return Repository(path)


@st.cache_resource
def get_llm(api_key: str, model: str):
    try:
        return GeminiClient(api_key, model), ""
    except LLMError as exc:
        return None, exc.message


repo = get_repo(str(settings.db_path))
llm, llm_problem = get_llm(settings.gemini_api_key, settings.gemini_model)
state = st.session_state
state.setdefault("page", "Briefs")
state.setdefault("selected", None)


def esc(text) -> str:
    return html.escape(str(text or ""))


def badge(status: str) -> str:
    css = STATUS_STYLE.get(status, ("muted", ""))[0]
    return f'<span class="sia-badge sia-{css}">{esc(status)}</span>'


def group(row: dict) -> str:
    return STATUS_STYLE.get(row["effective_status"], ("muted", "Failed"))[1]


def host_of(url: str) -> str:
    return (url or "").split("://")[-1].removeprefix("www.").strip("/")


def name_of(row: dict) -> str:
    return row["company_name"] or host_of(row["website"] or row["raw_input"])


def lead_from_row(row: dict) -> Lead:
    return Lead(raw_input=row["raw_input"], company_name=row["company_name"] or "", location=row["location"] or "",
                descriptor=row["descriptor"] or "", website=row["website"] or "",
                website_source=row["website_source"] or "")


def run_one(lead: Lead, lead_id: int | None = None):
    result = process_lead(lead, Fetcher(settings.request_timeout, settings.max_retries, settings.max_bytes),
                          llm, settings, use_search=state.get("use_search", True))
    repo.save_result(result, lead_id)
    return result


rows = repo.latest()
counts = {f: sum(group(r) == f for r in rows) for f in FILTERS}
to_review = sum(counts[f] for f in NEEDS_ACTION)

# ---------------- sidebar ----------------
with st.sidebar:
    st.markdown("### 🔎 Sales Intel")
    pages = ["Add leads", "Briefs", "Review queue", "Export"]
    labels = {"Review queue": f"Review queue · {to_review}" if to_review else "Review queue"}
    state.page = st.radio("Navigate", pages, index=pages.index(state.page), label_visibility="collapsed",
                          format_func=lambda p: labels.get(p, p))
    st.divider()
    if llm:
        st.caption(f"LLM: **{settings.gemini_model}**")
    else:
        st.error(f"LLM not configured: {llm_problem}")
    with st.expander("Settings & ICP"):
        settings.icp = st.text_area("Ideal customer profile (used for the B2B decision)", settings.icp, height=180)
        settings.max_pages = st.slider("Pages to read per website", 1, 6, settings.max_pages)
        state.use_search = st.checkbox("Suggest websites for name-only leads", value=state.get("use_search", True),
                                       help="DuckDuckGo suggestions are never used until a reviewer confirms them.")
        if st.button("Delete all saved results"):
            repo.clear()
            state.selected = None
            st.rerun()


# ---------------- add leads ----------------
def page_add_leads():
    st.title("Add leads")
    st.markdown('<p class="sia-meta">One lead per line: a website URL, or "Company – City ST". '
                'Or upload a CSV with company_name, website, location.</p>', unsafe_allow_html=True)
    left, right = st.columns([3, 2], gap="large")
    with left:
        text = st.text_area("Leads", SAMPLE.read_text(encoding="utf-8"), height=300, label_visibility="collapsed")
    with right:
        upload = st.file_uploader("Upload CSV or TXT", type=["csv", "txt"])
        st.caption("Uploading a file replaces the pasted list.")
    if upload:
        data = upload.getvalue()
        leads = parse_csv(data) if upload.name.lower().endswith(".csv") else parse_text(data.decode("utf-8", "replace"))
    else:
        leads = parse_text(text)
    leads, dupes = deduplicate(leads)
    with_site = sum(bool(l.website) for l in leads)
    st.markdown(f"**{len(leads)} leads** · {with_site} with a website · {len(leads) - with_site} name only")
    if dupes:
        st.warning(f"Skipped {len(dupes)} duplicate(s): " + "; ".join(d.raw_input for d in dupes))
    st.dataframe(pd.DataFrame([{"Company": l.company_name or "—", "Location": l.location or "—",
                                "Website": l.website or "needs lookup"} for l in leads]),
                 hide_index=True, width="stretch", height=min(38 * len(leads) + 40, 320))
    if st.button(f"Run analysis on {len(leads)} leads", type="primary", disabled=not leads):
        progress = st.progress(0.0)
        log = st.container()
        for i, lead in enumerate(leads, 1):
            progress.progress((i - 1) / len(leads), text=f"Researching {lead.display_name} ({i}/{len(leads)})")
            result = run_one(lead)
            log.markdown(f"{badge(result.status.value)} &nbsp; **{esc(lead.display_name)}**"
                         + (f" <span class='sia-meta'>· {esc(result.error_code)}</span>" if result.error_code else ""),
                         unsafe_allow_html=True)
        progress.progress(1.0, text="Done")
        if st.button("Open briefs", type="primary"):
            state.page = "Briefs"
            st.rerun()


# ---------------- workspace (briefs / review queue) ----------------
def evidence_quotes(brief: dict) -> dict[str, list[str]]:
    """Verified quotes per source URL."""
    by_url: dict[str, list[str]] = {}
    items = [ev for _, key in FIELDS for ev in brief[key]["evidence"]] + brief["b2b_qualification"]["evidence"]
    items += [q["based_on"] for q in brief["discovery_questions"]]
    for ev in items:
        by_url.setdefault(ev["source_url"].rstrip("/"), []).append(ev["quote"])
    return by_url


def evidence_panel(row: dict, brief: dict | None):
    st.markdown("##### Evidence")
    pages = [p for p in row["pages"] or [] if p["text"]]
    if not pages:
        st.caption("No pages were read for this lead.")
        return
    quotes = evidence_quotes(brief) if brief else {}

    def label(p):
        url = (p["final_url"] or p["url"]).rstrip("/")
        n = len(quotes.get(url, []))
        return f"{url.split('://')[-1]}  ·  {n} quote{'s' if n != 1 else ''}"

    order = sorted(pages, key=lambda p: -len(quotes.get((p["final_url"] or p["url"]).rstrip("/"), [])))
    page = st.selectbox("Source page", order, format_func=label, label_visibility="collapsed",
                        key=f"src-{row['brief_id']}")
    url = (page["final_url"] or page["url"]).rstrip("/")
    marked, found = highlight(page["text"], quotes.get(url, []), context=220)
    st.markdown(f'<div class="sia-source">{marked}</div>', unsafe_allow_html=True)
    st.caption(f"[Open page]({page['final_url'] or page['url']}) · {found} highlighted passage{'s' if found != 1 else ''}, "
               "each found word for word in the scraped text.")


def website_panel(row: dict, lead: Lead):
    st.markdown("##### Confirm the official website")
    candidates = {c["url"]: c for c in row["candidates"] or []}
    options = list(candidates)

    def describe(url):
        if url not in candidates:
            return "Enter the URL myself"
        c = candidates[url]
        return f"**{host_of(url)}** · {c['score']:.0%} match  \n:gray[{', '.join(c['reasons'])}]"

    if options:
        st.caption("Search suggestions, best match first. Check the site is the same company before confirming.")
    else:
        st.caption("No reliable suggestion was found. Paste the company's official website.")
    pick = st.radio("Suggested", options + ["Enter manually"], key=f"pick-{row['brief_id']}", format_func=describe,
                    label_visibility="collapsed") if options else "Enter manually"
    manual = st.text_input("Website URL", value=lead.website if pick == "Enter manually" else pick,
                           key=f"url-{row['brief_id']}-{pick}")
    if st.button("Use this website and research", type="primary", disabled=not normalize_url(manual),
                 key=f"go-{row['brief_id']}"):
        lead.website = normalize_url(manual)
        lead.website_source = "search_confirmed" if manual in options else "manual"
        with st.spinner(f"Researching {lead.display_name}…"):
            run_one(lead, row["lead_id"])
        st.rerun()


def edit_form(row: dict, brief: dict):
    with st.form(f"edit-{row['brief_id']}"):
        edited = {k: (dict(v) if isinstance(v, dict) else [dict(q) for q in v]) for k, v in brief.items()}
        for label, key in FIELDS:
            edited[key]["text"] = st.text_area(label, brief[key]["text"], height=80)
        choices = ["Yes", "No", "Insufficient evidence"]
        edited["b2b_qualification"]["decision"] = st.selectbox(
            "B2B qualification", choices, index=choices.index(brief["b2b_qualification"]["decision"]))
        edited["b2b_qualification"]["rationale"] = st.text_area("Rationale", brief["b2b_qualification"]["rationale"])
        for i, q in enumerate(edited["discovery_questions"]):
            q["question"] = st.text_input(f"Question {i + 1}", q["question"])
        reviewer = st.text_input("Reviewer", row["reviewer"] or "")
        notes = st.text_area("Notes", row["review_notes"] or "")
        if st.form_submit_button("Save and approve", type="primary"):
            repo.save_review(row["brief_id"], Status.APPROVED.value, edited, reviewer, notes)
            state.editing = None
            st.rerun()


def detail(row: dict):
    lead = lead_from_row(row)
    brief = row["final"]
    status = row["effective_status"]
    b2b = brief["b2b_qualification"] if brief else None
    st.markdown(f"## {esc(name_of(row))}")
    meta = " · ".join(filter(None, [row["location"], host_of(row["website"]),
                                    f"{len(row['pages'])} page{'s' if len(row['pages']) != 1 else ''} read" if row["pages"] else ""]))
    st.markdown(badge(status) + (f" &nbsp;{badge('B2B: ' + b2b['decision'])}" if b2b else "")
                + f' &nbsp;<span class="sia-meta">{esc(meta)}</span>', unsafe_allow_html=True)
    st.write("")

    if row["error_message"] and status != "Approved":
        css = "" if status in ("Website needed", "Needs review") else " bad"
        message = row["error_message"].replace("in the Review tab", "below")       # wording of older saved runs
        st.markdown(f'<div class="sia-alert{css}">{esc(message[:1].upper() + message[1:])}</div>',
                    unsafe_allow_html=True)
    for issue in [i for i in row["issues"] or [] if i["severity"] == "error"]:
        st.markdown(f'<div class="sia-alert"><b>Check:</b> {esc(issue["message"])}</div>', unsafe_allow_html=True)

    if status in ("Website needed", "Fetch failed") or not brief:
        website_panel(row, lead)
        if not brief:
            return

    main, evidence = st.columns([3, 2], gap="large")
    with main:
        sections = [(label, brief[key]["text"]) for label, key in FIELDS]
        sections.append((f"B2B qualification: {b2b['decision']}", b2b["rationale"]))
        st.markdown('<div class="sia-brief">' + "".join(f'<span class="sia-sec"><span class="sia-lbl">{esc(l)}</span>'
                                                           f'<span class="sia-txt">{esc(t)}</span></span>' for l, t in sections)
                    + "</div>", unsafe_allow_html=True)
        st.markdown("##### Discovery questions")
        st.markdown("".join(f'<p class="sia-q">{i}. {esc(q["question"])}<small>Based on: “{esc(q["based_on"]["quote"])}”</small></p>'
                            for i, q in enumerate(brief["discovery_questions"], 1)), unsafe_allow_html=True)
        warnings = [i for i in row["issues"] or [] if i["severity"] == "warning"]
        if warnings:
            with st.expander(f"{len(warnings)} minor note(s) from validation"):
                for w in warnings:
                    st.caption(f"{w['field']}: {w['message']}")
        if row["review_notes"]:
            st.caption(f"Reviewer notes ({row['reviewer'] or 'anonymous'}): {row['review_notes']}")
        st.write("")
        a, b, c, _ = st.columns([1, 1, 1.3, 2])
        if a.button("Edit", key=f"e-{row['brief_id']}", width="stretch"):
            state.editing = row["brief_id"]
        if b.button("Reject", key=f"r-{row['brief_id']}", width="stretch"):
            repo.save_review(row["brief_id"], Status.REJECTED.value, row["reviewed"], row["reviewer"] or "", row["review_notes"] or "")
            st.rerun()
        if c.button("Approve", key=f"a-{row['brief_id']}", type="primary", width="stretch",
                    disabled=status == "Approved"):
            repo.save_review(row["brief_id"], Status.APPROVED.value, row["reviewed"], row["reviewer"] or "", row["review_notes"] or "")
            st.rerun()
        if state.get("editing") == row["brief_id"]:
            edit_form(row, brief)
    with evidence:
        evidence_panel(row, brief)


def page_workspace(review_only: bool):
    st.title("Review queue" if review_only else "Briefs")
    if not rows:
        st.info("No results yet. Add leads and run the analysis first.")
        if st.button("Add leads", type="primary"):
            state.page = "Add leads"
            st.rerun()
        return
    available = [f for f in FILTERS if counts[f] and (not review_only or f in NEEDS_ACTION)]
    pills_col, search_col = st.columns([3, 2])
    with pills_col:
        chosen = st.pills("Status", available, selection_mode="multi", default=available,
                          format_func=lambda f: f"{f} {counts[f]}", label_visibility="collapsed",
                          key=f"filter-{review_only}") or available
    query = search_col.text_input("Search", placeholder="Search company or website", label_visibility="collapsed")
    rank = {"Needs review": 0, "Website needed": 1, "Failed": 2, "Ready": 3, "Approved": 4, "Rejected": 5}
    shown = [r for r in rows if group(r) in chosen
             and (not query or query.lower() in f"{name_of(r)} {r['website']} {r['location']}".lower())]
    shown.sort(key=lambda r: (rank.get(group(r), 9), name_of(r).lower()))
    if not shown:
        st.info("Nothing matches these filters.")
        return
    ids = [r["lead_id"] for r in shown]
    if state.selected not in ids:
        state.selected = ids[0]

    left, right = st.columns([1, 3.4], gap="medium")
    with left:
        with st.container(height=640, border=True):
            for r in shown:
                selected = r["lead_id"] == state.selected
                label = f"**{name_of(r)}**  \n" + (r["effective_status"] if selected
                                                      else f":{_tone(r)}[{r['effective_status']}]")
                if st.button(label, key=f"lead-{r['lead_id']}", width="stretch",
                             type="primary" if selected else "secondary"):
                    state.selected = r["lead_id"]
                    state.editing = None
                    st.rerun()
    with right:
        detail(next(r for r in shown if r["lead_id"] == state.selected))


def _tone(row: dict) -> str:
    return {"ok": "green", "warn": "orange", "info": "blue", "bad": "red"}.get(
        STATUS_STYLE.get(row["effective_status"], ("muted",))[0], "gray")


# ---------------- export ----------------
def page_export():
    st.title("Export")
    approved = [r for r in rows if r["effective_status"] == "Approved"]
    st.markdown(f'<p class="sia-meta">{len(rows)} leads · {len(approved)} approved. Reviewer edits replace '
                'generated text; the JSON keeps the original draft, the reviewed version and all evidence.</p>',
                unsafe_allow_html=True)
    only = st.toggle("Approved briefs only", value=bool(approved))
    data = approved if only else rows
    a, b, _ = st.columns([1, 1, 3])
    a.download_button("Download CSV", to_csv(data), "sales_briefs.csv", "text/csv", disabled=not data,
                      type="primary", width="stretch")
    b.download_button("Download JSON", to_json(data), "sales_briefs.json", "application/json", disabled=not data,
                      width="stretch")
    if data:
        st.dataframe(pd.DataFrame([{"Company": name_of(r), "Status": r["effective_status"],
                                    "B2B": (r["final"] or {}).get("b2b_qualification", {}).get("decision", "—")}
                                   for r in data]), hide_index=True, width="stretch")


if state.page == "Add leads":
    page_add_leads()
elif state.page == "Export":
    page_export()
else:
    page_workspace(review_only=state.page == "Review queue")
