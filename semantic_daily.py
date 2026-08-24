# coding: utf-8
"""arXiv ingestion, reporting, scope guards, and email helpers for AstroBrief."""
from __future__ import annotations

import datetime as dt
import os
import re
import smtplib
import ssl
import unicodedata
import xml.etree.ElementTree as ET
from email.message import EmailMessage

import markdown
import requests


ARXIV_API = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"

_SUPERSCRIPT_TRANS = str.maketrans(
    "0123456789+-=()",
    "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾",
)
_SUBSCRIPT_TRANS = str.maketrans(
    "0123456789+-=()",
    "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎",
)
_LATEX_SYMBOLS = {
    r"\lesssim": "≲",
    r"\gtrsim": "≳",
    r"\simeq": "≃",
    r"\approx": "≈",
    r"\sim": "≈",
    r"\leq": "≤",
    r"\geq": "≥",
    r"\le": "≤",
    r"\ge": "≥",
    r"\neq": "≠",
    r"\pm": "±",
    r"\mp": "∓",
    r"\times": "×",
    r"\cdot": "·",
    r"\propto": "∝",
    r"\infty": "∞",
    r"\odot": "☉",
    r"\oplus": "⊕",
    r"\circ": "°",
    r"\mu": "μ",
    r"\nu": "ν",
    r"\lambda": "λ",
    r"\alpha": "α",
    r"\beta": "β",
    r"\gamma": "γ",
    r"\delta": "δ",
    r"\epsilon": "ε",
    r"\zeta": "ζ",
    r"\eta": "η",
    r"\theta": "θ",
    r"\kappa": "κ",
    r"\rho": "ρ",
    r"\sigma": "σ",
    r"\tau": "τ",
    r"\phi": "φ",
    r"\chi": "χ",
    r"\psi": "ψ",
    r"\omega": "ω",
    r"\Gamma": "Γ",
    r"\Delta": "Δ",
    r"\Theta": "Θ",
    r"\Lambda": "Λ",
    r"\Sigma": "Σ",
    r"\Phi": "Φ",
    r"\Psi": "Ψ",
    r"\Omega": "Ω",
}
_LATEX_ACCENTS = {
    "'": "\u0301",   # acute
    "`": "\u0300",   # grave
    '"': "\u0308",   # diaeresis / umlaut
    "^": "\u0302",   # circumflex
    "~": "\u0303",   # tilde
    "=": "\u0304",   # macron
    ".": "\u0307",   # dot above
    "u": "\u0306",   # breve
    "v": "\u030c",   # caron
    "H": "\u030b",   # double acute
    "c": "\u0327",   # cedilla
    "k": "\u0328",   # ogonek
    "r": "\u030a",   # ring above
    "b": "\u0331",   # macron below
    "d": "\u0323",   # dot below
}
_LATEX_TEXT_LETTERS = {
    r"\aa": "å",
    r"\AA": "Å",
    r"\ae": "æ",
    r"\AE": "Æ",
    r"\oe": "œ",
    r"\OE": "Œ",
    r"\o": "ø",
    r"\O": "Ø",
    r"\ss": "ß",
    r"\l": "ł",
    r"\L": "Ł",
    r"\i": "ı",
    r"\j": "ȷ",
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _unicode_script(value: str, superscript: bool) -> str:
    """Render simple numeric scripts as Unicode and preserve named scripts readably."""
    value = value.strip()
    table = _SUPERSCRIPT_TRANS if superscript else _SUBSCRIPT_TRANS
    if value and all(ch in "0123456789+-=()" for ch in value):
        return value.translate(table)
    return ("^" if superscript else "_") + value


def _math_fragment_to_email_text(fragment: str) -> str:
    """Convert common arXiv inline LaTeX into email-safe Unicode/plain text.

    Email clients generally do not execute MathJax.  This intentionally targets
    the compact notation common in abstracts rather than trying to be a full TeX
    renderer.  The archived Markdown/report remains untouched; only email display
    uses this conversion.
    """
    value = fragment.strip()
    value = re.sub(r"\\left|\\right", "", value)
    value = re.sub(r"\^\s*\{?\\circ\}?", "°", value)

    # Resolve nested wrappers/scripts from the inside out.  A few passes cover
    # common constructs such as N_{\mathrm{HI,CNM}} and \mathrm{km~s^{-1}}.
    for _ in range(6):
        before = value
        value = re.sub(
            r"\\(?:mathrm|textrm|text|mathbf|mathit|mathsf|mathtt)\{([^{}]*)\}",
            r"\1",
            value,
        )
        value = re.sub(r"\\(?:rm|bf|it)\s*", "", value)
        value = re.sub(
            r"\\frac\{([^{}]*)\}\{([^{}]*)\}",
            lambda m: f"({m.group(1)})/({m.group(2)})",
            value,
        )
        value = re.sub(
            r"\\sqrt\{([^{}]*)\}",
            lambda m: f"√({m.group(1)})",
            value,
        )
        value = re.sub(
            r"\^\{([^{}]*)\}",
            lambda m: _unicode_script(m.group(1), True),
            value,
        )
        value = re.sub(
            r"_\{([^{}]*)\}",
            lambda m: _unicode_script(m.group(1), False),
            value,
        )
        value = re.sub(
            r"\^([+-]?\d+)",
            lambda m: _unicode_script(m.group(1), True),
            value,
        )
        value = re.sub(
            r"_([+-]?\d+)",
            lambda m: _unicode_script(m.group(1), False),
            value,
        )
        if value == before:
            break

    for latex, unicode_value in sorted(
        _LATEX_SYMBOLS.items(), key=lambda item: len(item[0]), reverse=True
    ):
        value = value.replace(latex, unicode_value)

    value = value.replace(r"\,", " ")
    value = value.replace(r"\:", " ")
    value = value.replace(r"\;", " ")
    value = value.replace(r"\!", "")
    value = value.replace(r"\ ", " ")
    value = value.replace(r"\%", "%")
    value = value.replace(r"\&", "&")
    value = value.replace(r"\_", "_")
    value = value.replace("~", " ")

    # Named solar/Earth subscripts are conventionally written without a literal
    # underscore in running prose.
    value = value.replace("_☉", "☉").replace("_⊕", "⊕")

    # Strip remaining grouping braces and degrade unknown commands gracefully.
    value = value.replace("{", "").replace("}", "")
    value = re.sub(r"\\([A-Za-z]+)", r"\1", value)

    # Add readable spacing around comparison/approximation operators.
    value = re.sub(r"\s*([≈≃≲≳≤≥≠±∓=<>])\s*", r" \1 ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _accent_target_to_text(target: str) -> str:
    """Resolve a one-letter TeX accent target such as z, {z}, or \\i."""
    target = target.strip()
    if target in {r"\i", r"\j"}:
        return _LATEX_TEXT_LETTERS[target]
    return target


def _compose_latex_accent(accent: str, target: str) -> str:
    """Compose a TeX text accent into a precomposed Unicode character when possible."""
    base = _accent_target_to_text(target)
    combining = _LATEX_ACCENTS.get(accent)
    if not combining or len(base) != 1:
        return base
    return unicodedata.normalize("NFC", base + combining)


def _latex_text_accents_to_unicode(text: str) -> str:
    """Render common TeX author-name/text accents as normal Unicode.

    arXiv author metadata can contain forms such as Sne{\\v{z}}ana,
    Stanimirovi{\\'c}, or D{\\'e}nes.  These are valid TeX but look broken in
    email clients, so convert them only in the email presentation layer.
    """
    target = r"(?:[A-Za-z]|\\[ij])"
    accent = r"['`\"\^~=\.uvHckrbd]"

    patterns = [
        # {\v{z}}, {\'\i}
        rf"\{{\\({accent})\{{({target})\}}\}}",
        # \v{z}, \'{c}
        rf"\\({accent})\{{({target})\}}",
        # {\'c}, {\'\i}
        rf"\{{\\({accent})({target})\}}",
        # \'c, \v z (without braces; whitespace tolerated)
        rf"\\({accent})\s*({target})",
    ]
    value = text
    for pattern in patterns:
        value = re.sub(
            pattern,
            lambda m: _compose_latex_accent(m.group(1), m.group(2)),
            value,
        )

    # TeX special letters used in personal and place names.  Require that the
    # command is not followed by another alphabetic character so \o does not
    # accidentally consume the beginning of an unrelated command such as \omega.
    for latex, unicode_value in sorted(
        _LATEX_TEXT_LETTERS.items(), key=lambda item: len(item[0]), reverse=True
    ):
        command = re.escape(latex)
        value = re.sub(
            rf"\{{{command}\}}|{command}(?![A-Za-z])",
            unicode_value,
            value,
        )

    return value


def latex_to_email_text(text: str) -> str:
    """Convert TeX math and text accents in report text for robust email rendering."""
    converted = re.sub(
        r"\\\[(.+?)\\\]",
        lambda m: _math_fragment_to_email_text(m.group(1)),
        text,
        flags=re.S,
    )
    converted = re.sub(
        r"\\\((.+?)\\\)",
        lambda m: _math_fragment_to_email_text(m.group(1)),
        converted,
        flags=re.S,
    )
    converted = re.sub(
        r"(?<!\\)\$(.+?)(?<!\\)\$",
        lambda m: _math_fragment_to_email_text(m.group(1)),
        converted,
        flags=re.S,
    )
    return _latex_text_accents_to_unicode(converted)


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

    # Convert common TeX notation only for the email payload.  The Markdown files
    # committed to the repository retain the original arXiv abstract verbatim.
    email_text = latex_to_email_text(markdown_text)
    html_body = markdown.markdown(
        email_text,
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
    msg.set_content(email_text)
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