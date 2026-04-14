"""Azure DevOps helpers: work items, PR threads, and review comments."""

from __future__ import annotations

import os
import re
from typing import Any

import requests

SEVERITY_THRESHOLD = int(os.environ.get("SEVERITY_THRESHOLD", 7))
MAX_ISSUES = int(os.environ.get("MAX_ISSUES", 10))

_SEVERITY_ICON = {10: "🔴", 9: "🔴", 8: "🟠", 7: "🟡"}


def _strip_html(text: str) -> str:
    """Remove HTML tags from ADO rich-text fields."""
    return re.sub(r"<[^>]+>", " ", text).strip()


def fetch_pr_work_items(
    collection_uri: str,
    project: str,
    repo_id: str,
    pr_id: str,
    access_token: str,
) -> list[dict[str, Any]]:
    """Return work item details (title, description, acceptance criteria) for all
    work items linked to the given pull request.

    Args:
        collection_uri: Azure DevOps organization URL (e.g. https://dev.azure.com/myorg/).
        project: ADO project name.
        repo_id: Repository GUID.
        pr_id: Pull request ID.
        access_token: ADO System.AccessToken for API authentication.

    Returns:
        A list of dicts with keys: id, title, description, acceptance_criteria.
    """
    base = collection_uri.rstrip("/")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    url = (
        f"{base}/{project}/_apis/git/repositories/{repo_id}"
        f"/pullRequests/{pr_id}/workitems?api-version=7.1"
    )
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code != 200:
        print(f"[WARNING] Could not fetch PR work items (HTTP {resp.status_code}). Skipping.")
        return []

    wi_refs = resp.json().get("value", [])
    if not wi_refs:
        print("[INFO] No work items linked to this PR.")
        return []

    print(f"[INFO] Found {len(wi_refs)} linked work item(s). Fetching details ...")

    work_items: list[dict[str, Any]] = []
    fields = "System.Title,System.Description,Microsoft.VSTS.Common.AcceptanceCriteria"
    for ref in wi_refs:
        wi_id = ref.get("id")
        if not wi_id:
            continue
        wi_url = f"{base}/{project}/_apis/wit/workitems/{wi_id}?fields={fields}&api-version=7.1"
        wi_resp = requests.get(wi_url, headers=headers, timeout=30)
        if wi_resp.status_code != 200:
            print(f"[WARNING] Could not fetch work item #{wi_id} (HTTP {wi_resp.status_code}).")
            continue
        f = wi_resp.json().get("fields", {})
        work_items.append({
            "id": wi_id,
            "title": f.get("System.Title", ""),
            "description": _strip_html(f.get("System.Description") or ""),
            "acceptance_criteria": _strip_html(f.get("Microsoft.VSTS.Common.AcceptanceCriteria") or ""),
        })
    return work_items


def fetch_previous_review(
    collection_uri: str,
    project: str,
    repo_id: str,
    pr_id: str,
    access_token: str,
) -> str:
    """Return the body of the most recent bot review comment on the PR, or empty string.

    Args:
        collection_uri: Azure DevOps organization URL.
        project: ADO project name.
        repo_id: Repository GUID.
        pr_id: Pull request ID.
        access_token: ADO System.AccessToken for API authentication.

    Returns:
        The markdown content of the previous review comment, or "" if none exists.
    """
    base = collection_uri.rstrip("/")
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    threads_url = (
        f"{base}/{project}/_apis/git/repositories/{repo_id}"
        f"/pullRequests/{pr_id}/threads?api-version=7.1"
    )
    resp = requests.get(threads_url, headers=headers, timeout=30)
    if resp.status_code != 200:
        return ""
    for thread in resp.json().get("value", []):
        comments = thread.get("comments", [])
        if comments and comments[0].get("content", "").startswith("## \U0001f916 AI Code Review"):
            return comments[0]["content"]
    return ""


def delete_previous_review_comments(
    collection_uri: str,
    project: str,
    repo_id: str,
    pr_id: str,
    access_token: str,
) -> None:
    """Delete any existing PR threads previously posted by this bot.

    Args:
        collection_uri: Azure DevOps organization URL.
        project: ADO project name.
        repo_id: Repository GUID.
        pr_id: Pull request ID.
        access_token: ADO System.AccessToken for API authentication.
    """
    base = collection_uri.rstrip("/")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    threads_url = (
        f"{base}/{project}/_apis/git/repositories/{repo_id}"
        f"/pullRequests/{pr_id}/threads?api-version=7.1"
    )
    resp = requests.get(threads_url, headers=headers, timeout=30)
    if resp.status_code != 200:
        print(f"[WARNING] Could not list PR threads (HTTP {resp.status_code}). Skipping cleanup.")
        return

    deleted = 0
    for thread in resp.json().get("value", []):
        comments = thread.get("comments", [])
        if not comments:
            continue
        first = comments[0].get("content", "")
        if not first.startswith("## 🤖 AI Code Review"):
            continue
        thread_id = thread["id"]
        for comment in comments:
            comment_id = comment["id"]
            del_url = (
                f"{base}/{project}/_apis/git/repositories/{repo_id}"
                f"/pullRequests/{pr_id}/threads/{thread_id}/comments/{comment_id}?api-version=7.1"
            )
            requests.delete(del_url, headers=headers, timeout=30)
        deleted += 1

    if deleted:
        print(f"[INFO] Deleted {deleted} previous review thread(s).")


def build_comment(issues: list[dict[str, Any]]) -> str:
    """Build the markdown body for the ADO PR review thread.

    Args:
        issues: Filtered and sorted list of issues returned by the model.

    Returns:
        A markdown string ready to be posted as a PR comment.
    """
    if not issues:
        return (
            "## 🤖 AI Code Review\n\n"
            "✅ No issues with severity ≥ 7/10 detected in the changes of this PR."
        )

    lines: list[str] = [
        "## 🤖 AI Code Review",
        "",
        f"Found **{len(issues)}** issue(s) with severity **≥ {SEVERITY_THRESHOLD}/10** "
        f"in the changes introduced by this PR (top {MAX_ISSUES} shown).",
        "",
    ]

    for issue in issues:
        sev: int = issue.get("severity", 0)
        icon = _SEVERITY_ICON.get(sev, "🟡")
        file_ref = f"`{issue.get('file', 'unknown')}`"
        if issue.get("line"):
            file_ref += f" line {issue['line']}"

        lines += [
            f"### {icon} [{sev}/10] {issue.get('title', 'Untitled Issue')}",
            f"**Category:** `{issue.get('category', 'unknown')}` &nbsp;|&nbsp; **File:** {file_ref}",
            "",
            f"**Problem:** {issue.get('description', '')}",
            "",
            f"**Suggestion:** {issue.get('suggestion', '')}",
            "",
            "---",
            "",
        ]

    return "\n".join(lines)


def post_pr_comment(issues: list[dict[str, Any]]) -> None:
    """Post the review summary as a thread comment on the Azure DevOps PR.

    Args:
        issues: Filtered and sorted list of issues to include in the comment.
                Pass an empty list to post a 'no issues found' message.
    """
    collection_uri = os.environ.get("SYSTEM_TEAMFOUNDATIONCOLLECTIONURI", "").rstrip("/")
    project = os.environ.get("SYSTEM_TEAMPROJECT", "")
    repo_id = os.environ.get("BUILD_REPOSITORY_ID", "")
    pr_id = os.environ.get("SYSTEM_PULLREQUEST_PULLREQUESTID", "")
    access_token = os.environ.get("SYSTEM_ACCESSTOKEN", "")

    missing = [
        name
        for name, val in [
            ("SYSTEM_TEAMFOUNDATIONCOLLECTIONURI", collection_uri),
            ("SYSTEM_TEAMPROJECT", project),
            ("BUILD_REPOSITORY_ID", repo_id),
            ("SYSTEM_PULLREQUEST_PULLREQUESTID", pr_id),
            ("SYSTEM_ACCESSTOKEN", access_token),
        ]
        if not val
    ]
    if missing:
        print(f"[WARNING] Missing Azure DevOps env vars: {', '.join(missing)}. Skipping PR comment.")
        return

    url = (
        f"{collection_uri}/{project}/_apis/git/repositories/{repo_id}"
        f"/pullRequests/{pr_id}/threads?api-version=7.1"
    )
    payload = {
        "comments": [
            {"parentCommentId": 0, "content": build_comment(issues), "commentType": 1}
        ],
        "status": 1,
    }
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    print(f"[INFO] Review comment posted to PR #{pr_id}.")


def print_issues(issues: list[dict[str, Any]]) -> None:
    """Print a human-readable summary of issues to stdout.

    Args:
        issues: Filtered and sorted list of issues returned by the model.
    """
    if not issues:
        print(f"\nNo issues with severity >= {SEVERITY_THRESHOLD}/10 found. ✅")
        return

    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  {len(issues)} issue(s) with severity >= {SEVERITY_THRESHOLD}/10 (sorted by severity)")
    print(sep)

    for issue in issues:
        sev = issue.get("severity", "?")
        print(f"\n  [{sev}/10] {issue.get('title', 'Unknown')}")
        print(f"  Category : {issue.get('category', 'unknown')}")
        file_info = issue.get("file", "unknown")
        if issue.get("line"):
            file_info += f" (line {issue['line']})"
        print(f"  File     : {file_info}")
        print(f"  Problem  : {issue.get('description', '')}")
        print(f"  Fix      : {issue.get('suggestion', '')}")

    print(f"\n{sep}\n")
