# coding: utf-8
"""Small GitHub Issues helper used by AstroBrief."""
from __future__ import annotations

import os

import requests


def _repository() -> tuple[str, str]:
    """Resolve owner/repository from GitHub Actions or explicit environment variables."""
    full_name = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if "/" in full_name:
        owner, repo = full_name.split("/", 1)
        return owner, repo

    owner = os.environ.get("ASTROBRIEF_REPO_OWNER", "").strip()
    repo = os.environ.get("ASTROBRIEF_REPO_NAME", "").strip()
    if owner and repo:
        return owner, repo
    raise RuntimeError(
        "Repository is not configured. Run inside GitHub Actions or set "
        "ASTROBRIEF_REPO_OWNER and ASTROBRIEF_REPO_NAME."
    )


def make_github_issue(
    title: str,
    body: str | None = None,
    assignee: str | None = None,
    closed: bool = False,
    labels: list[str] | None = None,
    TOKEN: str = "",
) -> bool:
    """Create an issue without making issue failures fatal to delivery."""
    if not TOKEN:
        print("[WARN] GitHub token is unavailable; skip issue creation")
        return False

    owner, repo = _repository()
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data: dict[str, object] = {"title": title, "body": body or ""}
    if assignee:
        data["assignees"] = [assignee]
    if labels:
        data["labels"] = labels

    try:
        response = requests.post(url, json=data, headers=headers, timeout=30)
        if response.status_code == 422 and ("labels" in data or "assignees" in data):
            response = requests.post(
                url,
                json={"title": title, "body": body or ""},
                headers=headers,
                timeout=30,
            )
    except requests.RequestException as exc:
        print(f'[WARN] Could not create issue "{title}": {exc}')
        return False

    if response.status_code == 201:
        print(f'[OK] Created issue "{title}"')
        if closed:
            issue_url = response.json().get("url")
            if issue_url:
                requests.patch(
                    issue_url,
                    json={"state": "closed"},
                    headers=headers,
                    timeout=30,
                )
        return True

    print(f'[WARN] Could not create issue "{title}": {response.status_code} {response.text}')
    return False
