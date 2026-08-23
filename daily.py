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


def main(token: str) -> None:
    issue_title, papers = fetch_daily_papers()
    scored, summary = score_papers(papers)
    scored, summary = apply_final_scope_guard(scored, summary)
    selected = [p for p in scored if p["priority"] in {"A", "B"}]
    full_report, email_report = build_reports(issue_title, scored, summary)

    run_date = dt.date.today().isoformat()
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
    args = parser.parse_args()
    main(args.token)
