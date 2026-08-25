# coding: utf-8
"""Quiet Academic HTML email presentation for AstroBrief.

This module intentionally stays presentation-only: ranking, scope decisions,
and archived score data remain owned by the semantic pipeline.  The email shows
only the final A/B priority, matched research topics, bibliographic metadata,
full abstract, and arXiv/PDF links.
"""
from __future__ import annotations

import datetime as dt
import html
import os
import smtplib
import ssl
from email.message import EmailMessage

from semantic_daily import latex_to_email_text


# Low-saturation, Morandi-inspired palette chosen for long-form daily reading.
BACKGROUND = "#F5F3EF"
CARD = "#FBFAF7"
PRIMARY = "#293139"
SECONDARY = "#6F716D"
BORDER = "#DDD9D2"
A_ACCENT = "#627C8A"  # muted blue-gray
B_ACCENT = "#8B9D93"  # muted sage
LINK = "#5F7889"

TOPIC_LABELS = {
    "atomic_ism": "Atomic ISM",
    "molecular_clouds": "Molecular clouds",
    "star_formation": "Star formation",
    "feedback_bubbles": "Feedback & bubbles",
    "turbulence": "Turbulence",
    "magnetic_fields": "Magnetic fields",
    "astrochemistry": "Astrochemistry",
    "massive_star_formation": "Massive star formation",
    "galactic_ism": "Galactic ISM",
    "halo_cgm": "Halo / CGM",
    "ism_methods": "ISM methods",
}


def _email_text(value: object) -> str:
    """Convert common arXiv TeX forms to email-safe Unicode/plain text."""
    return latex_to_email_text(str(value or "")).strip()


def _escape(value: object) -> str:
    return html.escape(_email_text(value), quote=True)


def _topic_labels(paper: dict) -> list[str]:
    values = list(paper.get("top_topics") or [])[:3]
    if not values and paper.get("best_positive_topic"):
        values = [paper["best_positive_topic"]]
    labels = []
    for value in values:
        raw = str(value).strip()
        if not raw:
            continue
        label = TOPIC_LABELS.get(raw, raw.replace("_", " ").strip().title())
        if label not in labels:
            labels.append(label)
    return labels


def _selected(scored: list[dict]) -> list[dict]:
    return [p for p in scored if p.get("priority") in {"A", "B"}]


def _card(paper: dict) -> str:
    priority = str(paper.get("priority") or "B")
    accent = A_ACCENT if priority == "A" else B_ACCENT
    label = "HIGH MATCH" if priority == "A" else "RELEVANT"
    title = _escape(paper.get("title"))
    authors = _escape(", ".join(paper.get("authors") or [])) or "—"
    categories = paper.get("categories") or []
    if isinstance(categories, str):
        categories_text = categories
    else:
        categories_text = " · ".join(str(v) for v in categories)
    categories_html = _escape(categories_text) or "—"
    topics = " · ".join(_topic_labels(paper)) or "—"
    topics_html = html.escape(topics, quote=True)
    abstract = _escape(paper.get("abstract")) or "—"
    arxiv_url = html.escape(str(paper.get("main_page") or ""), quote=True)
    pdf_url = html.escape(str(paper.get("pdf") or ""), quote=True)

    return f"""
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
      style="margin:0 0 16px 0;border:1px solid {BORDER};border-left:3px solid {accent};border-radius:10px;background:{CARD};">
      <tr><td style="padding:22px 24px 20px 24px;">
        <div style="margin-bottom:10px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
          <span style="display:inline-block;min-width:22px;padding:3px 7px;border-radius:5px;background:{accent};color:#ffffff;font-size:12px;font-weight:700;text-align:center;">{priority}</span>
          <span style="margin-left:9px;color:{accent};font-size:11px;font-weight:700;letter-spacing:.08em;">{label}</span>
        </div>
        <div style="font-family:Georgia,'Times New Roman',serif;color:{PRIMARY};font-size:21px;line-height:1.30;font-weight:700;margin:0 0 11px 0;">{title}</div>
        <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:{LINK};font-size:13px;line-height:1.55;margin:0 0 15px 0;">
          <span style="color:{SECONDARY};font-size:10px;font-weight:700;letter-spacing:.08em;">MATCHED TOPICS</span>&nbsp;&nbsp;{topics_html}
        </div>
        <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:{PRIMARY};font-size:13px;line-height:1.65;margin:0 0 4px 0;">
          <span style="color:{SECONDARY};font-size:10px;font-weight:700;letter-spacing:.08em;">AUTHORS</span>&nbsp;&nbsp;{authors}
        </div>
        <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:{PRIMARY};font-size:13px;line-height:1.65;margin:0 0 15px 0;">
          <span style="color:{SECONDARY};font-size:10px;font-weight:700;letter-spacing:.08em;">CATEGORIES</span>&nbsp;&nbsp;{categories_html}
        </div>
        <div style="height:1px;background:{BORDER};font-size:0;line-height:0;margin:0 0 14px 0;">&nbsp;</div>
        <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:{SECONDARY};font-size:10px;font-weight:700;letter-spacing:.08em;margin:0 0 7px 0;">ABSTRACT</div>
        <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:{PRIMARY};font-size:14px;line-height:1.72;margin:0 0 16px 0;">{abstract}</div>
        <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:13px;font-weight:600;">
          <a href="{arxiv_url}" style="color:{LINK};text-decoration:none;margin-right:22px;">arXiv ↗</a>
          <a href="{pdf_url}" style="color:{LINK};text-decoration:none;">PDF ↗</a>
        </div>
      </td></tr>
    </table>
    """


def render_email_html(batch_date: str, scored: list[dict], summary: dict) -> str:
    """Render the final A/B digest as a restrained, email-client-safe HTML page."""
    selected = _selected(scored)
    a_count = sum(p.get("priority") == "A" for p in selected)
    b_count = sum(p.get("priority") == "B" for p in selected)
    try:
        date_label = dt.date.fromisoformat(batch_date).strftime("%d %b %Y").upper()
    except ValueError:
        date_label = batch_date

    cards = "".join(_card(p) for p in selected)
    if not cards:
        cards = f"""
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
          style="border:1px solid {BORDER};border-radius:10px;background:{CARD};">
          <tr><td style="padding:26px 24px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:{SECONDARY};font-size:14px;line-height:1.7;">
            No A/B recommendation in this announcement batch.
          </td></tr>
        </table>
        """

    return f"""<!doctype html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:{BACKGROUND};">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:{BACKGROUND};">
    <tr><td align="center" style="padding:28px 12px 36px 12px;">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:680px;">
        <tr><td align="center" style="padding:4px 12px 22px 12px;">
          <div style="font-family:Georgia,'Times New Roman',serif;color:{PRIMARY};font-size:36px;line-height:1.1;font-weight:700;letter-spacing:-.02em;">AstroBrief</div>
          <div style="margin-top:7px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:{SECONDARY};font-size:10px;font-weight:700;letter-spacing:.22em;">DAILY LITERATURE BRIEF</div>
          <div style="margin-top:12px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:{PRIMARY};font-size:12px;">{html.escape(date_label)}</div>
          <div style="height:1px;background:{BORDER};font-size:0;line-height:0;margin:16px 0 13px 0;">&nbsp;</div>
          <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:{PRIMARY};font-size:14px;">
            <strong>{len(selected)}</strong> papers selected&nbsp;&nbsp;·&nbsp;&nbsp;<strong>{a_count}</strong> A&nbsp;&nbsp;·&nbsp;&nbsp;<strong>{b_count}</strong> B
          </div>
        </td></tr>
        <tr><td>{cards}</td></tr>
        <tr><td align="center" style="padding:12px 18px 0 18px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:{SECONDARY};font-size:11px;line-height:1.65;">
          Semantic screening for ISM, star formation, and related gaseous environments.<br>
          SPECTER2 · domain evidence · local zero-shot NLI
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def render_email_text(batch_date: str, scored: list[dict], summary: dict) -> str:
    """Plain-text alternative with the same user-facing information as HTML."""
    selected = _selected(scored)
    a_count = sum(p.get("priority") == "A" for p in selected)
    b_count = sum(p.get("priority") == "B" for p in selected)
    lines = [
        "AstroBrief — Daily Literature Brief",
        batch_date,
        f"{len(selected)} papers selected · {a_count} A · {b_count} B",
        "",
    ]
    if not selected:
        lines.append("No A/B recommendation in this announcement batch.")
        return "\n".join(lines)

    for p in selected:
        priority = p.get("priority", "B")
        label = "HIGH MATCH" if priority == "A" else "RELEVANT"
        topics = " · ".join(_topic_labels(p)) or "—"
        categories = p.get("categories") or []
        if not isinstance(categories, str):
            categories = " · ".join(str(v) for v in categories)
        lines.extend([
            f"[{priority}] {label}",
            _email_text(p.get("title")),
            f"Matched topics: {topics}",
            f"Authors: {_email_text(', '.join(p.get('authors') or [])) or '—'}",
            f"Categories: {_email_text(categories) or '—'}",
            "Abstract:",
            _email_text(p.get("abstract")) or "—",
            f"arXiv: {p.get('main_page', '')}",
            f"PDF: {p.get('pdf', '')}",
            "",
        ])
    return "\n".join(lines)


def send_email(batch_date: str, scored: list[dict], summary: dict) -> None:
    """Send the production AstroBrief email using the structured A/B results."""
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USERNAME")
    pwd = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("SMTP_FROM")
    recipients = os.environ.get("EMAIL_TO")
    if not all([host, user, pwd, sender, recipients]):
        raise RuntimeError("SMTP is not fully configured")

    selected = _selected(scored)
    plain = render_email_text(batch_date, scored, summary)
    rich = render_email_html(batch_date, scored, summary)

    msg = EmailMessage()
    msg["Subject"] = f"AstroBrief · {batch_date} · {len(selected)} papers"
    msg["From"] = sender
    msg["To"] = recipients
    msg.set_content(plain)
    msg.add_alternative(rich, subtype="html")

    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as smtp:
            smtp.login(user, pwd)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(user, pwd)
            smtp.send_message(msg)
    print("[OK] Mail sent to", recipients)
