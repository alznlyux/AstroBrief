# coding: utf-8
"""Standalone production entry point for AstroBrief."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib

from github_issue import make_github_issue
from semantic_daily import (
    apply_final_scope_guard,
    build_reports,
    fetch_daily_papers,
    send_email,
)
from semantic_recommender import score_papers


def main(token: str, force: bool = False) -> None:
    run_date = dt.date.today().isoformat()
    state_dir = pathlib.Path("state")
    sent_marker = state_dir / f"{run_date}.sent"

    if sent_marker.exists() and not force:
        print(f"[SKIP] AstroBrief already sent for {run_date}: {sent_marker}")
        return

    issue_title, papers = fetch_daily_papers()
    scored, summary = score_papers(papers)
    scored, summary = apply_final_scope_guard(scored, summary)
    selected = [p for p in scored if p["priority"] in {"A", "B"}]
    full_report, email_report = build_reports(issue_title, scored, summary)

    brief_dir = pathlib.Path("briefs")
    score_dir = pathlib.Path("scores")
    brief_dir.mkdir(exist_ok=True)
    score_dir.mkdir(exist_ok=True)

    (brief_dir / f"{run_date}.md").write_text(full_report, encoding="utf-8")
    pathlib.Path("LATEST.md").write_text(full_report, encoding="utf-8")
    (score_dir / f"{run_date}.json").write_text(
        json.dumps({"summary": summary, "papers": scored}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    send_email(email_report, len(selected))

    # Persist a marker immediately after a successful SMTP send.  The workflow's
    # final commit step runs with `if: always()`, so this marker is still pushed
    # even if a later non-email step fails.  A fallback schedule can therefore
    # avoid sending the same daily brief twice.
    state_dir.mkdir(exist_ok=True)
    sent_marker.write_text(
        json.dumps(
            {
                "sent_date": run_date,
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
        help="Run even if today's sent marker already exists.",
    )
    args = parser.parse_args()
    main(args.token, force=args.force)
