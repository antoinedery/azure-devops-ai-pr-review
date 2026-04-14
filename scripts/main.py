#!/usr/bin/env python3
"""
AI Pull Request code reviewer for Azure DevOps.
Uses Microsoft Azure AI Foundry for code analysis.

Usage (local):
    AZURE_AD_TOKEN=<token> FOUNDRY_URL=<endpoint> python scripts/main.py

In CI the Azure DevOps pipeline sets all env vars automatically.

Auto-injected by Azure DevOps when the pipeline runs on a PR:
    SYSTEM_ACCESSTOKEN                      Built-in token to post PR comments.
    SYSTEM_PULLREQUEST_TARGETBRANCH         e.g. refs/heads/dev
    SYSTEM_TEAMFOUNDATIONCOLLECTIONURI      e.g. https://dev.azure.com/myorg/
    SYSTEM_TEAMPROJECT                      e.g. MyProject
    BUILD_REPOSITORY_ID                     Repository GUID
    SYSTEM_PULLREQUEST_PULLREQUESTID        PR number
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# Ensure the scripts folder is on the path regardless of working directory
sys.path.insert(0, str(Path(__file__).parent))

from azure_devops import (  # noqa: E402
    SEVERITY_THRESHOLD,
    MAX_ISSUES,
    delete_previous_review_comments,
    fetch_previous_review,
    fetch_pr_work_items,
    post_pr_comment,
    print_issues,
)
from foundry import FOUNDRY_URL, call_ai  # noqa: E402


# ── Git helpers ────────────────────────────────────────────────────────────────

def _get_pr_diff(target_branch: str) -> str:
    """Return the git diff between the current HEAD and the target branch."""
    # Three-dot diff: changes introduced in this branch vs the common ancestor
    result = subprocess.run(
        ["git", "diff", f"origin/{target_branch}...HEAD", "--diff-filter=ACMRT"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    """Entry point: fetch the PR diff, call the AI model, and post results to ADO."""
    if not FOUNDRY_URL:
        print("ERROR: FOUNDRY_URL must be set (Azure AI Foundry endpoint URL).")
        sys.exit(1)

    # Strip refs/heads/ prefix injected by Azure DevOps
    raw_branch = os.environ.get("SYSTEM_PULLREQUEST_TARGETBRANCH", "main")
    target_branch = raw_branch.replace("refs/heads/", "").strip()

    print(f"[INFO] Fetching diff against origin/{target_branch} ...")
    diff = _get_pr_diff(target_branch)

    if not diff.strip():
        print("[INFO] No trackable file changes detected in this PR.")
        post_pr_comment([])
        return

    print(f"[INFO] Diff size: {len(diff):,} chars. Calling Azure AI Foundry ...")

    collection_uri = os.environ.get("SYSTEM_TEAMFOUNDATIONCOLLECTIONURI", "")
    project = os.environ.get("SYSTEM_TEAMPROJECT", "")
    repo_id = os.environ.get("BUILD_REPOSITORY_ID", "")
    pr_id = os.environ.get("SYSTEM_PULLREQUEST_PULLREQUESTID", "")
    access_token = os.environ.get("SYSTEM_ACCESSTOKEN", "")
    ado_context = all([collection_uri, project, repo_id, pr_id, access_token])

    work_items: list[dict[str, Any]] = []
    if ado_context:
        work_items = fetch_pr_work_items(collection_uri, project, repo_id, pr_id, access_token)

    previous_review = ""
    if ado_context:
        previous_review = fetch_previous_review(collection_uri, project, repo_id, pr_id, access_token)
        if previous_review:
            print("[INFO] Previous review found — including for consistency.")

    raw_issues: list[dict[str, Any]] = call_ai(diff, work_items, previous_review)
    print(f"[INFO] Model returned {len(raw_issues)} total issue(s).")

    # Filter, sort, and cap
    filtered = sorted(
        [i for i in raw_issues if isinstance(i.get("severity"), int) and i["severity"] >= SEVERITY_THRESHOLD],
        key=lambda x: x["severity"],
        reverse=True,
    )
    if len(filtered) > MAX_ISSUES:
        print(f"[INFO] Showing top {MAX_ISSUES} issue(s) by severity (set MAX_ISSUES env var to change).")
    high_severity = filtered[:MAX_ISSUES]

    print_issues(high_severity)
    if ado_context:
        delete_previous_review_comments(collection_uri, project, repo_id, pr_id, access_token)
    post_pr_comment(high_severity)


if __name__ == "__main__":
    main()
