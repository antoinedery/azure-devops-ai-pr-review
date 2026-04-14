"""Azure AI Foundry client: prompt construction and model call."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "review.md"

MAX_DIFF_CHARS = 60_000  # Keep well within model token limits

# Azure AI Foundry endpoint URL — must be set via FOUNDRY_URL env var.
# Responses API: https://<resource>.cognitiveservices.azure.com/openai/responses?api-version=...
FOUNDRY_URL = os.environ.get("FOUNDRY_URL", "")

# Deployment/model name — required when FOUNDRY_URL is a Responses API URL
# (deployment is not embedded in the path for that format).
AZURE_DEPLOYMENT = os.environ.get("AZURE_DEPLOYMENT", "")

# Azure AI Foundry authentication via Service Principal.
# AZURE_AD_TOKEN is fetched by the pipeline's AzureCLI@2 task.
AZURE_AD_TOKEN = os.environ.get("AZURE_AD_TOKEN", "")

REVIEW_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")


def call_ai(diff: str, work_items: list[dict[str, Any]], previous_review: str = "") -> list[dict[str, Any]]:
    """Send the diff to the Azure AI Foundry endpoint and return parsed issues.

    Args:
        diff: The git diff string to review.
        work_items: Linked ADO work items to validate the code against.
        previous_review: The previous review comment body, if any, for consistency.

    Returns:
        A list of issue dicts as returned by the model (unsorted, unfiltered).
    """
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n\n[... diff truncated to fit context window ...]"

    if work_items:
        wi_lines = [
            "\nLinked Product Backlog Items / User Stories:",
            "Cross-check the code changes against each work item below. Flag any acceptance",
            "criteria that are not fully implemented, implemented incorrectly, or contradicted",
            "by the code. Use category 'requirements' for those findings.\n",
        ]
        for wi in work_items:
            wi_lines.append(f"--- Work Item #{wi['id']}: {wi['title']}")
            if wi.get("description"):
                wi_lines.append(f"Description: {wi['description']}")
            if wi.get("acceptance_criteria"):
                wi_lines.append(f"Acceptance Criteria: {wi['acceptance_criteria']}")
            wi_lines.append("")
        work_items_section = "\n".join(wi_lines) + "\n"
    else:
        work_items_section = ""

    if previous_review:
        previous_review_section = (
            "\nPrevious review comment (from an earlier run on this PR):\n"
            "Use this as a reference for consistency — do not simply copy it, but ensure "
            "issues that still exist in the diff are flagged at a comparable severity.\n"
            f"```\n{previous_review[:3000]}\n```\n"
        )
    else:
        previous_review_section = ""

    prompt = (
        REVIEW_PROMPT
        .replace("{diff}", diff)
        .replace("{work_items_section}", work_items_section)
        .replace("{previous_review_section}", previous_review_section)
    )

    if not AZURE_AD_TOKEN:
        raise ValueError("AZURE_AD_TOKEN is not set — ensure the AzureCLI@2 task ran successfully")
    auth_token = AZURE_AD_TOKEN

    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": AZURE_DEPLOYMENT,
        "input": prompt,
        "max_output_tokens": 32000,  # reasoning models consume tokens for chain-of-thought before output
    }
    resp = requests.post(FOUNDRY_URL, headers=headers, json=payload, timeout=120)

    if not resp.ok:
        print(f"[DEBUG] Response status: {resp.status_code}")
        print(f"[DEBUG] Response body: {resp.text[:1000]}")
    resp.raise_for_status()
    data = resp.json()

    # output is a list: first item is "reasoning" (no content), second is "message".
    # Find the first item with type=="message" and extract its text.
    raw_content: str | None = None
    for item in data.get("output", []):
        if item.get("type") == "message":
            try:
                raw_content = item["content"][0]["text"]
            except (KeyError, IndexError):
                pass
            break

    if not raw_content or not raw_content.strip():
        print("[WARNING] Model returned an empty response. Returning no issues.")
        return []

    content = raw_content.strip()

    # Strip markdown code fences if the model wrapped the JSON anyway
    if content.startswith("```"):
        parts = content.split("```")
        raw = parts[1]
        if raw.startswith("json"):
            raw = raw[4:]
        content = raw.strip()

    if not content:
        print("[WARNING] Content was empty after stripping fences. Returning no issues.")
        return []

    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        print(f"[ERROR] Model returned invalid JSON: {exc}\nContent preview: {content[:500]}")
        return []
