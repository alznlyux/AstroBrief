# coding: utf-8
"""Resolve the real arXiv astro-ph announcement batch and fetch its metadata.

The arXiv `/list/astro-ph/new` page defines the public announcement batch.
`submittedDate` in the Atom API is a submission timestamp and is not equivalent
 to an announcement date, especially across weekends, moderation delays, and
cross-lists.  This module therefore uses the list page only as a manifest of
arXiv IDs, then retrieves structured metadata for those IDs from the Atom API.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

import requests

ARXIV_NEW_LIST = "https://arxiv.org/list/astro-ph/new"
ARXIV_API = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _user_agent() -> str:
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    contact = os.environ.get("ASTROBRIEF_CONTACT", "").strip()
    if repository:
        return f"AstroBrief/1.0 (+https://github.com/{repository})"
    if contact:
        return f"AstroBrief/1.0 ({contact})"
    return "AstroBrief/1.0"


class _AnnouncementListParser(HTMLParser):
    """Extract the listing date plus new-submission/cross-list arXiv IDs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_h3 = False
        self._h3_parts: list[str] = []
        self.section: str | None = None
        self.batch_date: str | None = None
        self.ids: list[str] = []
        self.new_count = 0
        self.cross_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "h3":
            self._in_h3 = True
            self._h3_parts = []
            return

        if tag.lower() != "a" or self.section not in {"new", "cross"}:
            return

        href = dict(attrs).get("href") or ""
        match = re.fullmatch(r"/abs/([^?#]+)", href)
        if not match:
            return
        paper_id = re.sub(r"v\d+$", "", match.group(1))
        if not paper_id or paper_id in self.ids:
            return
        self.ids.append(paper_id)
        if self.section == "new":
            self.new_count += 1
        else:
            self.cross_count += 1

    def handle_data(self, data: str) -> None:
        if self._in_h3:
            self._h3_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "h3" or not self._in_h3:
            return
        text = _clean("".join(self._h3_parts))
        self._in_h3 = False
        self._h3_parts = []

        date_match = re.match(r"Showing new listings for (.+)$", text, flags=re.I)
        if date_match:
            date_text = date_match.group(1).strip()
            try:
                parsed = dt.datetime.strptime(date_text, "%A, %d %B %Y").date()
            except ValueError as exc:
                raise RuntimeError(
                    f"Could not parse arXiv announcement date {date_text!r}"
                ) from exc
            self.batch_date = parsed.isoformat()
            return

        lower = text.lower()
        if lower.startswith("new submissions"):
            self.section = "new"
        elif lower.startswith("cross-lists"):
            self.section = "cross"
        elif lower.startswith("replacements"):
            self.section = "replacement"


def parse_announcement_manifest(html: str) -> tuple[str, list[str], dict[str, int]]:
    """Parse one `/list/astro-ph/new` page into batch date and candidate IDs."""
    parser = _AnnouncementListParser()
    parser.feed(html)
    parser.close()

    if not parser.batch_date:
        raise RuntimeError("arXiv new-list page did not expose an announcement date")
    if not parser.ids:
        raise RuntimeError(
            f"arXiv announcement batch {parser.batch_date} contained no new/cross-list IDs"
        )

    counts = {
        "new": parser.new_count,
        "cross": parser.cross_count,
        "total": len(parser.ids),
    }
    return parser.batch_date, parser.ids, counts


def fetch_announcement_manifest() -> tuple[str, list[str], dict[str, int]]:
    """Fetch the current public astro-ph announcement manifest from arXiv."""
    response = requests.get(
        ARXIV_NEW_LIST,
        params={"show": 2000},
        headers={"User-Agent": _user_agent()},
        timeout=120,
    )
    response.raise_for_status()
    batch_date, ids, counts = parse_announcement_manifest(response.text)
    print(
        f"[INFO] arXiv announcement batch {batch_date}: "
        f"{counts['new']} new + {counts['cross']} cross-list = {counts['total']} candidates"
    )
    return batch_date, ids, counts


def _parse_atom_entries(content: bytes) -> dict[str, dict]:
    root = ET.fromstring(content)
    papers: dict[str, dict] = {}
    for entry in root.findall(f"{ATOM}entry"):
        raw_id = _clean(entry.findtext(f"{ATOM}id", default=""))
        paper_id = raw_id.rsplit("/", 1)[-1]
        paper_id = re.sub(r"v\d+$", "", paper_id)
        if not paper_id:
            continue

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

        papers[paper_id] = {
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
    return papers


def fetch_metadata_for_ids(ids: list[str], chunk_size: int = 100) -> list[dict]:
    """Retrieve complete structured Atom metadata for an announcement manifest."""
    if not ids:
        return []

    by_id: dict[str, dict] = {}
    for start in range(0, len(ids), chunk_size):
        chunk = ids[start : start + chunk_size]
        response = requests.get(
            ARXIV_API,
            params={
                "id_list": ",".join(chunk),
                "start": 0,
                "max_results": len(chunk),
            },
            headers={"User-Agent": _user_agent()},
            timeout=120,
        )
        response.raise_for_status()
        by_id.update(_parse_atom_entries(response.content))
        if start + chunk_size < len(ids):
            time.sleep(3)

    missing = [paper_id for paper_id in ids if paper_id not in by_id]
    if missing:
        preview = ", ".join(missing[:10])
        suffix = " ..." if len(missing) > 10 else ""
        raise RuntimeError(
            f"Atom API returned incomplete metadata: missing {len(missing)} of "
            f"{len(ids)} announcement IDs ({preview}{suffix})"
        )

    return [by_id[paper_id] for paper_id in ids]


def fetch_daily_papers() -> tuple[str, list[dict]]:
    """Return the current astro-ph announcement batch and structured metadata."""
    batch_date, ids, _counts = fetch_announcement_manifest()
    papers = fetch_metadata_for_ids(ids)
    issue_title = f"Latest astro-ph submissions for {batch_date}"
    print(f"[INFO] Retrieved metadata for all {len(papers)} announcement candidates")
    return issue_title, papers
