# coding: utf-8
"""arXiv ingestion, reporting, scope guards, and email helpers for AstroBrief."""
from __future__ import annotations

import datetime as dt
import os
import re
import smtplib
import ssl
import xml.etree.ElementTree as ET
from email.message import EmailMessage

import markdown
import requests


ARXIV_API = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _user_agent() -> str:
    """Build a descriptive User-Agent without hard-coding one repository."""
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    contact = os.environ.get("ASTROBRIEF_CONTACT", "").strip()
    if repository:
        return f"AstroBrief/1.0 (+https://github.com/{repository})"
    if contact:
        return f"AstroBrief/1.0 ({contact})"
    return "AstroBrief/1.0"


def _fetch_arxiv_day(day: dt.date) -> list[dict]:
    """Fetch every paper carrying an astro-ph category for one UTC calendar day."""
    stamp = day.strftime("%Y%m%d")
    query = f"cat:astro-ph.* AND submittedDate:[{stamp}0000 TO {stamp}2359]"
    params = {
        "search_query": query,
        "start": 0,
        "max_results": 2000,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    response = requests.get(
        ARXIV_API,
        params=params,
        headers={"User-Agent": _user_agent()},
        timeout=120,
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)

    papers = []
    seen = set()
    for entry in root.findall(f"{ATOM}entry"):
        raw_id = _clean(entry.findtext(f"{ATOM}id", default=""))
        paper_id = raw_id.rsplit("/", 1)[-1]
        paper_id = re.sub(r"v\d+$", "", paper_id)
        if not paper_id or paper_id in seen:
            continue
        seen.add(paper_id)

        title = _clean(entry.findtext(f"{ATOM}title", default=""))
        abstract = _clean(entry.findtext(f"{ATOM}summary", default=""))
        authors = [
            _clean(author.findtext(f"{ATOM}name", default=""))
            for author in entry.findall(f"{ATOM}author")
        ]
        categories = [
            node.attrib.get("term", "")
            for node in entry.findall(f"{ATOM}category")
            if node.attrib.get("term", "")
        ]
        primary_node = entry.find(f"{ARXIV}primary_category")
        primary = primary_node.attrib.get("term", "") if primary_node is not None else ""

        if not any(cat.startswith("astro-ph.") for cat in categories):
            continue

        papers.append(
            {
                "id": paper_id,
                "title": title,
                "authors": authors,
                "subjects": "; ".join(categories),
                "categories": categories,
                "primary_category": primary,
                "abstract": abstract,
                "main_page": f"https://arxiv.org/abs/{paper_id}",
                "pdf": f"https://arxiv.org/pdf/{paper_id}.pdf",
            }
        )
    return papers


def fetch_daily_papers(max_lookback_days: int = 7) -> tuple[str, list[dict]]:
    """Return the most recent calendar day that has astro-ph submissions.

    AstroBrief uses the official Atom API instead of scraping the HTML
    /list/astro-ph/new page. A category query naturally includes astro-ph
    cross-lists, while revisions are not treated as separate list-page entries.
    """
    today = dt.datetime.utcnow().date()
    last_error = None
    for offset in range(max_lookback_days + 1):
        day = today - dt.timedelta(days=offset)
        try:
            papers = _fetch_arxiv_day(day)
        except Exception as exc:
            last_error = exc
            print(f"[WARN] arXiv API failed for {day}: {exc}")
            continue
        if papers:
            issue_title = f"Latest astro-ph submissions for {day.isoformat()}"
            print(f"[INFO] Retrieved {len(papers)} astro-ph papers for {day}")
            return issue_title, papers
        print(f"[INFO] No astro-ph submissions for {day}; checking previous day")

    if last_error is not None:
        raise RuntimeError(
            f"No recent astro-ph day could be retrieved; last API error: {last_error}"
        )
    raise RuntimeError("No astro-ph submissions found in the lookback window")


def _true_galactic_ism_rescue(p: dict) -> bool:
    """High-precision final check for scope-level Galactic/local ISM rescues."""
    title = p.get("title", "")
    text = title + "\n" + p.get("abstract", "")
    h = lambda pattern, value=text: re.search(pattern, value, flags=re.I) is not None

    fermi_hi = h(r"Fermi Bubbles?") and h(
        r"\b(?:neutral gas|neutral clouds?|H\s*I\s+(?:data|clouds?|gas|emission)|N[_ ]?HI)\b"
    )
    cmz_title = h(r"\b(?:CMZ|Central Molecular Zone)\b", title)
    galactic_center_gas = h(r"\bGalactic Cent(?:re|er)\b") and h(
        r"\b(?:molecular gas|molecular clouds?|atomic gas|neutral gas|gas cloud|gas clouds|"
        r"interstellar medium|ISM|dense gas|CMZ|Central Molecular Zone)\b"
    )
    interstellar_magnetic = h(
        r"\binterstellar\b.*\b(?:magnetic|reconnection|filament|gas|medium)\b|"
        r"\b(?:magnetic|reconnection|filament)\b.*\binterstellar\b",
        title,
    )
    explicit_hi_title = h(
        r"\b(?:neutral gas|neutral hydrogen|H\s*I\s+(?:clouds?|gas|emission|absorption|survey))\b",
        title,
    )
    return (
        fermi_hi
        or cmz_title
        or galactic_center_gas
        or interstellar_magnetic
        or explicit_hi_title
    )


def apply_final_scope_guard(
    scored: list[dict], summary: dict
) -> tuple[list[dict], dict]:
    """Undo any overbroad scope rescue before report/email generation."""
    for p in scored:
        reason = str(p.get("scope_reason", ""))
        previous = p.get("pre_scope_priority")
        if reason.startswith("scope rescue") and previous in {"SKIP", "C"}:
            if not _true_galactic_ism_rescue(p):
                p["priority"] = previous
                p["scope_reason"] = (
                    "final scope guard reverted an overbroad Galactic-center rescue"
                )

    scored.sort(
        key=lambda p: (
            {"SKIP": 0, "C": 1, "B": 2, "A": 3}[p["priority"]],
            float(p.get("score", 0.0)),
        ),
        reverse=True,
    )
    summary = dict(summary)
    for priority in ("A", "B", "C", "SKIP"):
        summary[priority] = sum(p["priority"] == priority for p in scored)
    return scored, summary


def paper_block(p: dict) -> str:
    authors = ", ".join(p.get("authors", []))
    top_topics = ", ".join(p.get("top_topics", [])[:3])
    return "\n".join(
        [
            f"#### [{p['priority']}] {p['title']}",
            f"- **Score:** {p['score']:.1f}  ",
            f"- **Topics:** {top_topics}  ",
            f"- **Authors:** {authors}  ",
            f"- **Subjects:** {p.get('subjects', '')}  ",
            f"- **arXiv:** [{p['main_page']}]({p['main_page']})  ",
            f"- **PDF:** [{p['pdf']}]({p['pdf']})  ",
            f"- **Abstract:** {p['abstract']}",
            "",
        ]
    )


def build_reports(
    issue_title: str, scored: list[dict], summary: dict
) -> tuple[str, str]:
    selected = [p for p in scored if p["priority"] in {"A", "B"}]
    boundary = [p for p in scored if p["priority"] == "C"]
    date = dt.date.today().isoformat()

    header = [
        f"# AstroBrief — {issue_title}",
        "",
        "Semantic ISM / star-formation screening: **SPECTER2 + domain evidence + local zero-shot NLI**.",
        "No paid model API is used.",
        "",
        f"### Today: {len(selected)} recommended papers",
        f"- Priority A: **{summary['A']}**",
        f"- Priority B: **{summary['B']}**",
        f"- Boundary C (archive only): **{summary['C']}**",
        f"- Screened astro-ph candidates: **{summary['candidate_count']}**",
        "",
    ]
    if not selected:
        header.extend(["There is no A/B recommendation today.", ""])

    selected_text = "\n".join(paper_block(p) for p in selected)
    email_report = "\n".join(header) + selected_text
    email_report += f"\n\nGenerated by AstroBrief on {date}.\n"

    full_report = email_report
    full_report += "\n\n### Boundary candidates (C; not emailed as recommendations)\n\n"
    if boundary:
        for p in boundary:
            full_report += (
                f"- **{p['title']}** — `{p.get('best_positive_topic')}` — "
                f"[{p['id']}]({p['main_page']})\n"
            )
    else:
        full_report += "- None\n"
    return full_report, email_report


def send_email(markdown_text: str, n_selected: int) -> None:
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USERNAME")
    pwd = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("SMTP_FROM")
    recipients = os.environ.get("EMAIL_TO")
    if not all([host, user, pwd, sender, recipients]):
        print("[WARN] SMTP not configured; skip email")
        return

    html_body = markdown.markdown(
        markdown_text,
        extensions=["extra", "nl2br", "sane_lists", "toc", "pymdownx.magiclink"],
    )
    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
    body {{ font:14px/1.6 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial; color:#111 }}
    a {{ text-decoration:none }} code,pre {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace }}
    </style></head><body>{html_body}</body></html>"""

    msg = EmailMessage()
    msg["Subject"] = (
        f"AstroBrief · {dt.date.today().isoformat()} · {n_selected} papers"
    )
    msg["From"] = sender
    msg["To"] = recipients
    msg.set_content(markdown_text)
    msg.add_alternative(html, subtype="html")

    if port == 465:
        with smtplib.SMTP_SSL(
            host, port, context=ssl.create_default_context()
        ) as smtp:
            smtp.login(user, pwd)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(user, pwd)
            smtp.send_message(msg)
    print("[OK] Mail sent to", recipients)
