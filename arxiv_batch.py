# coding: utf-8
"""Ingest the real arXiv astro-ph announcement directly from its daily listing.

AstroBrief is a daily announcement consumer, not a historical arXiv database.
The public `/list/astro-ph/new` page is therefore the source of truth for both
batch membership and paper metadata. One page request provides the announced
batch date, section boundaries, arXiv IDs, titles, authors, subjects, and
abstracts. The production path intentionally does not depend on the Atom API.
"""
from __future__ import annotations

import datetime as dt
import os
import re

import requests
from bs4 import BeautifulSoup

ARXIV_NEW_LIST = "https://arxiv.org/list/astro-ph/new"


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


def _parse_batch_date(soup: BeautifulSoup) -> str:
    for heading in soup.find_all("h3"):
        text = _clean(heading.get_text(" ", strip=True))
        match = re.match(r"Showing new listings for (.+)$", text, flags=re.I)
        if not match:
            continue
        date_text = match.group(1).strip()
        try:
            parsed = dt.datetime.strptime(date_text, "%A, %d %B %Y").date()
        except ValueError as exc:
            raise RuntimeError(
                f"Could not parse arXiv announcement date {date_text!r}"
            ) from exc
        weekday = date_text.split(",", 1)[0].strip()
        if parsed.strftime("%A") != weekday:
            raise RuntimeError(
                f"arXiv announcement weekday/date mismatch: {date_text!r}"
            )
        return parsed.isoformat()
    raise RuntimeError("arXiv new-list page did not expose an announcement date")


def _parse_section_starts(soup: BeautifulSoup) -> dict[str, int]:
    starts: dict[str, int] = {}
    for anchor in soup.find_all("a", href=re.compile(r"^#item\d+$")):
        href = str(anchor.get("href") or "")
        text = _clean(anchor.get_text(" ", strip=True)).lower()
        item = int(href.removeprefix("#item"))
        if text.startswith("new submissions"):
            starts["new"] = item
        elif text.startswith("cross-lists"):
            starts["cross"] = item
        elif text.startswith("replacements"):
            starts["replacement"] = item

    if "new" not in starts:
        raise RuntimeError("arXiv new-list page did not expose the New submissions boundary")
    return starts


def _extract_categories(subjects_text: str) -> list[str]:
    """Extract ordered arXiv category codes from the listing's Subjects text."""
    categories: list[str] = []
    for value in re.findall(r"\(([^()]+)\)", subjects_text):
        code = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9.-]+", code):
            continue
        if "." not in code and "-" not in code:
            continue
        if code not in categories:
            categories.append(code)
    return categories


def _extract_authors(authors_div) -> list[str]:
    linked = [
        _clean(anchor.get_text(" ", strip=True))
        for anchor in authors_div.find_all("a")
        if _clean(anchor.get_text(" ", strip=True))
    ]
    if linked:
        return linked

    text = _clean(authors_div.get_text(" ", strip=True))
    text = re.sub(r"^Authors?:\s*", "", text, flags=re.I)
    return [part.strip() for part in text.split(",") if part.strip()]


def _parse_listing_entries(soup: BeautifulSoup) -> list[dict]:
    """Parse every displayed article across New, Cross-list, and Replacement dl blocks."""
    content = soup.find("div", id="content") or soup
    listings = content.find_all("dl")
    if not listings:
        raise RuntimeError("arXiv new-list page did not contain the article listings")

    pairs = []
    for section_index, listing in enumerate(listings, start=1):
        dt_list = listing.find_all("dt", recursive=False)
        dd_list = listing.find_all("dd", recursive=False)
        if len(dt_list) != len(dd_list):
            raise RuntimeError(
                f"arXiv listing section {section_index} dt/dd mismatch: "
                f"{len(dt_list)} ids vs {len(dd_list)} metadata blocks"
            )
        pairs.extend(zip(dt_list, dd_list))

    if not pairs:
        raise RuntimeError("arXiv article listings were empty")

    # `?show=2000` should expose the entire daily page. The page-level total
    # includes New submissions, Cross-lists, and Replacements, so validate only
    # after concatenating all listing sections.
    page_text = _clean(content.get_text(" ", strip=True))
    total_match = re.search(r"Total of\s+(\d+)\s+entries", page_text, flags=re.I)
    if total_match and int(total_match.group(1)) != len(pairs):
        raise RuntimeError(
            f"arXiv listing appears incomplete: page says {total_match.group(1)} entries "
            f"but {len(pairs)} were parsed across {len(listings)} sections"
        )

    entries: list[dict] = []
    for index, (dt_node, dd_node) in enumerate(pairs, start=1):
        abs_anchor = dt_node.find("a", attrs={"title": "Abstract"})
        if abs_anchor is None:
            abs_anchor = dt_node.find("a", href=re.compile(r"^/abs/"))
        href = str(abs_anchor.get("href") or "") if abs_anchor is not None else ""
        match = re.fullmatch(r"/abs/([^?#/]+)", href)
        if not match:
            raise RuntimeError(f"Could not parse arXiv ID for listing item {index}")
        paper_id = re.sub(r"v\d+$", "", match.group(1))

        title_div = dd_node.select_one("div.list-title")
        authors_div = dd_node.select_one("div.list-authors")
        subjects_div = dd_node.select_one("div.list-subjects")
        abstract_node = dd_node.select_one("p.mathjax") or dd_node.find("p")
        if None in (title_div, authors_div, subjects_div, abstract_node):
            raise RuntimeError(
                f"Incomplete arXiv metadata block for {paper_id}: "
                "title/authors/subjects/abstract are all required"
            )

        title = _clean(title_div.get_text(" ", strip=True))
        title = re.sub(r"^Title:\s*", "", title, flags=re.I)
        authors = _extract_authors(authors_div)
        subjects_text = _clean(subjects_div.get_text(" ", strip=True))
        subjects_text = re.sub(r"^Subjects?:\s*", "", subjects_text, flags=re.I)
        categories = _extract_categories(subjects_text)
        abstract = _clean(abstract_node.get_text(" ", strip=True))

        if not title or not authors or not abstract or not categories:
            raise RuntimeError(
                f"Incomplete parsed metadata for {paper_id}: "
                f"title={bool(title)} authors={bool(authors)} "
                f"categories={bool(categories)} abstract={bool(abstract)}"
            )
        if not any(cat.startswith("astro-ph.") for cat in categories):
            raise RuntimeError(
                f"Listing item {paper_id} has no astro-ph category: {categories}"
            )

        entries.append(
            {
                "id": paper_id,
                "title": title,
                "authors": authors,
                "subjects": "; ".join(categories),
                "categories": categories,
                "primary_category": categories[0],
                "abstract": abstract,
                "main_page": f"https://arxiv.org/abs/{paper_id}",
                "pdf": f"https://arxiv.org/pdf/{paper_id}.pdf",
            }
        )
    return entries


def parse_announcement_page(html: str) -> tuple[str, list[dict], dict[str, int]]:
    """Parse one complete `/list/astro-ph/new` page into production candidates."""
    soup = BeautifulSoup(html, "html.parser")
    batch_date = _parse_batch_date(soup)
    starts = _parse_section_starts(soup)
    all_entries = _parse_listing_entries(soup)

    new_start = starts["new"]
    cross_start = starts.get("cross")
    replacement_start = starts.get("replacement")

    if new_start != 0:
        raise RuntimeError(f"Unexpected arXiv New submissions boundary: #item{new_start}")

    if replacement_start is None:
        candidate_total = len(all_entries)
    else:
        candidate_total = replacement_start - 1
    if candidate_total < 1 or candidate_total > len(all_entries):
        raise RuntimeError(
            f"Invalid replacement boundary {replacement_start!r} for {len(all_entries)} entries"
        )

    if cross_start is None:
        new_count = candidate_total
    else:
        new_count = cross_start - 1
        if new_count < 0 or new_count > candidate_total:
            raise RuntimeError(
                f"Invalid cross-list boundary {cross_start!r} for {candidate_total} candidates"
            )
    cross_count = candidate_total - new_count

    candidates = all_entries[:candidate_total]
    ids = [paper["id"] for paper in candidates]
    if len(ids) != len(set(ids)):
        raise RuntimeError("Duplicate arXiv IDs appeared inside the new/cross-list candidate set")

    counts = {"new": new_count, "cross": cross_count, "total": candidate_total}
    return batch_date, candidates, counts


def parse_announcement_manifest(html: str) -> tuple[str, list[str], dict[str, int]]:
    batch_date, papers, counts = parse_announcement_page(html)
    return batch_date, [paper["id"] for paper in papers], counts


def fetch_announcement_page() -> tuple[str, list[dict], dict[str, int]]:
    response = requests.get(
        ARXIV_NEW_LIST,
        params={"show": 2000},
        headers={"User-Agent": _user_agent()},
        timeout=120,
    )
    response.raise_for_status()
    batch_date, papers, counts = parse_announcement_page(response.text)
    print(
        f"[INFO] arXiv announcement batch {batch_date}: "
        f"{counts['new']} new + {counts['cross']} cross-list = "
        f"{counts['total']} candidates (metadata parsed from listing page)"
    )
    return batch_date, papers, counts


def fetch_announcement_manifest() -> tuple[str, list[str], dict[str, int]]:
    batch_date, papers, counts = fetch_announcement_page()
    return batch_date, [paper["id"] for paper in papers], counts


def fetch_daily_papers() -> tuple[str, list[dict]]:
    batch_date, papers, _counts = fetch_announcement_page()
    issue_title = f"Latest astro-ph submissions for {batch_date}"
    print(f"[INFO] Parsed complete listing metadata for all {len(papers)} candidates")
    return issue_title, papers
