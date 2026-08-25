# coding: utf-8
"""Standalone production entry point for AstroBrief."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re

from arxiv_batch import fetch_daily_papers
from email_ui import send_email
from github_issue import make_github_issue
from semantic_daily import apply_final_scope_guard, build_reports
from semantic_recommender import score_papers


def _extract_batch_date(issue_title: str) -> str:
    """Extract the arXiv announcement-batch date from the ingestion title."""
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", issue_title)
    if not match:
        raise RuntimeError(f"Could not determine arXiv batch date from: {issue_title!r}")
    return match.group(1)


def _smtp_is_configured() -> bool:
    required = (
        "SMTP_HOST",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_FROM",
        "EMAIL_TO",
    )
    return all(os.environ.get(name, "").strip() for name in required)


def main(token: str, force: bool = False) -> None:
    # Always resolve arXiv's real public announcement batch first. Deduplication
    # is tied to that batch, not to a UTC submission timestamp or workflow date.
    # This is important around weekends, holidays, moderation delays, cross-lists,
    # delayed announcements, and US daylight-saving-time transitions.
    issue_title, papers = fetch_daily_papers()
    batch_date = _extract_batch_date(issue_title)

    state_dir = pathlib.Path("state")
    sent_marker = state_dir / f"arxiv-{batch_date}.sent"

    if sent_marker.exists() and not force:
        print(
            f"[SKIP] AstroBrief already sent for arXiv batch {batch_date}: "
            f"{sent_marker}"
        )
        return

    scored, summary = score_papers(papers)
    scored, summary = apply_final_scope_guard(scored, summary)
    selected = [p for p in scored if p["priority"] in {"A", "B"}]
    full_report, _email_report = build_reports(issue_title, scored, summary)

    run_date = dt.datetime.now(dt.timezone.utc).date().isoformat()
    brief_dir = pathlib.Path("briefs")
    score_dir = pathlib.Path("scores")
    brief_dir.mkdir(exist_ok=True)
    score_dir.mkdir(exist_ok=True)

    (brief_dir / f"{run_date}.md").write_text(full_report, encoding="utf-8")
    pathlib.Path("LATEST.md").write_text(full_report, encoding="utf-8")
    (score_dir / f"{run_date}.json").write_text(
        json.dumps(
            {
                "run_date": run_date,
                "arxiv_batch_date": batch_date,
                "summary": summary,
                "papers": scored,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # A missing SMTP configuration must never create a successful-send marker.
    if not _smtp_is_configured():
        raise RuntimeError("SMTP is not fully configured; refusing to mark batch as sent")

    # Email rendering is presentation-only: the structured A/B decisions and
    # matched topics come directly from the existing semantic pipeline.
    send_email(batch_date, scored, summary)

    # Persist the marker immediately after a successful SMTP send. The workflow's
    # final commit step runs with `if: always()`, so this marker is still pushed if
    # a later non-email step (for example issue creation) fails.
    state_dir.mkdir(exist_ok=True)
    sent_marker.write_text(
        json.dumps(
            {
                "arxiv_batch_date": batch_date,
                "run_date": run_date,
                "sent_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "recommended_papers": len(selected),
                "issue_title": issue_title,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[OK] Wrote sent marker {sent_marker}")

    make_github_issue(
        title=f"AstroBrief · {issue_title}",
        body=full_report,
        labels=None,
        TOKEN=token,
    )
    print("[SUMMARY]", json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run AstroBrief once")
    parser.add_argument("-t", "--token", default="")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even if the retrieved arXiv batch already has a sent marker.",
    )
    args = parser.parse_args()
    main(args.token, force=args.force)
