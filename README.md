# azure-devops-ai-pr-review

Automated AI code review for Azure DevOps pull requests. Uses Azure AI Foundry to detect security vulnerabilities, logic bugs, performance issues, and requirements drift, then posts a structured comment directly on the PR.

---

## How it works

1. A PR is opened or updated in your ADO repository
2. The pipeline checks out the repo and computes the git diff against the target branch
3. Any work items linked to the PR are fetched and included in the prompt
4. The diff is sent to an Azure AI Foundry model (Responses API)
5. Issues above the severity threshold are posted as a PR thread comment

---

## Prerequisites

- An Azure DevOps organization and project
- An Azure subscription with access to Azure AI Foundry

---

## Step 1 - Get this repository into ADO

Choose one option:

**Option A - Import into Azure Repos (recommended approach)**

1. In ADO, go to **Repos → Import repository**
2. Set source URL to `https://github.com/antoinedery/azure-devops-ai-pr-review`
3. Give it a name (e.g. `ai-pr-review-templates`) and import
4. In your pipeline file, reference it as an ADO repo:

```yaml
resources:
  repositories:
    - repository: templates
      type: git
      name: "<your-project>/ai-pr-review-templates"
      ref: "refs/heads/main"
```

**Option B - Reference GitHub directly (stays in sync with updates)**

1. In ADO, go to **Project Settings → Service connections → New service connection**
2. Choose **GitHub**, authenticate, and give it a name (e.g. `github-antoinedery`)
3. In your pipeline file, reference it as a GitHub repo:

```yaml
resources:
  repositories:
    - repository: templates
      type: github
      name: "antoinedery/azure-devops-ai-pr-review"
      ref: "refs/heads/main"
      endpoint: "github-antoinedery"
```

---

## Step 1 - Deploy a model in Azure AI Foundry

1. Go to [Azure AI Foundry](https://ai.azure.com) and open or create a project
2. Navigate to **My assets → Models + endpoints → Deploy model**
3. Select a model - recommended: **gpt-5.1-codex-mini**
4. Set a deployment name (e.g. `gpt-5.1-codex-mini`) - you will need this later
5. After deployment, copy the **endpoint URL** from the deployment details page

The endpoint URL follows this format:

```
https://<resource>.cognitiveservices.azure.com/openai/responses?api-version=2025-01-01-preview
```

---

## Step 2 - Create an Azure service connection in ADO

This allows the pipeline to authenticate against Azure AI Foundry using a Service Principal.

1. In ADO, go to **Project Settings → Service connections → New service connection**
2. Choose **Azure Resource Manager**
3. Select **Service principal (automatic)** and choose the subscription where your AI Foundry resource lives
4. Give it a name (e.g. `CloudConnection`) - you will use this as `serviceConnection` in the pipeline
5. Grant the service principal the **Cognitive Services User** role on the Azure AI Foundry resource:
   - Go to the AI Foundry resource in the Azure Portal
   - **Access control (IAM) → Add role assignment**
   - Role: `Cognitive Services User`
   - Assign to the service principal created above

---

## Step 3 - Store the Foundry URL as a pipeline variable

Never hardcode the endpoint URL in your pipeline file.

1. In ADO, go to **Pipelines → Library → + Variable group**
2. Name it `pr-code-review-secrets`
3. Add a variable: `FOUNDRY_URL` = the endpoint URL from Step 1
4. Mark it as **secret**

---

## Step 4 - Allow the pipeline to post PR comments

The pipeline uses `System.AccessToken` to post comments on the PR. You need to grant it permission to contribute to pull requests.

1. Go to **Project Settings → Repositories → [your repo] → Security**
2. Find the **[project] Build Service** identity
3. Set **Contribute to pull requests** to **Allow**

---

## Step 5 - Add the pipeline to your repository

Create a pipeline YAML file in your repository (e.g. `ai-pr-review.yml`), using the `resources` block from whichever option you chose in Step 1:

```yaml
# Required secrets (variable group: pr-code-review-secrets):
#   FOUNDRY_URL - Full endpoint URL for your AI Foundry deployment

trigger: none

resources:
  repositories:
    - repository: templates
      type: git # or: github
      name: "<your-project>/ai-pr-review-templates" # or: "antoinedery/azure-devops-ai-pr-review"
      ref: "refs/heads/main"
      # endpoint: "github-antoinedery"                # only needed for type: github

pr:
  branches:
    include:
      - "*"
  drafts: false

variables:
  - group: pr-code-review-secrets

extends:
  template: ai-pr-review-extends.yml@templates
  parameters:
    serviceConnection: "CloudConnection" # Azure service connection from Step 2
    foundryUrl: $(FOUNDRY_URL) # from variable group
    azureDeployment: "gpt-5.1-codex-mini" # deployment name from Step 1
    severityThreshold: 7 # 1–10, issues below this are ignored
    maxIssues: 5 # max issues posted to the PR
```

Then in ADO:

1. Go to **Pipelines → New pipeline**
2. Select **Azure Repos Git** and choose your repository
3. Select **Existing Azure Pipelines YAML file** and point to `ai-pr-review.yml`
4. Save (do not run yet)

---

## Step 6 - Add it as a branch policy (build validation)

This makes the AI review run on every PR and optionally block merges.

1. Go to **Project Settings → Repositories → [your repo] → Policies**
2. Under **Branch Policies**, click on the branch you want to protect (e.g. `main` or `dev`)
3. Scroll to **Build Validation** and click **+**
4. Select the pipeline you created in Step 5
5. Configure:
   - **Trigger**: Manual _(each API call has a cost - manual trigger lets developers run the review on demand rather than on every push)_
   - **Policy requirement**: Optional _(recommended - AI failures should not block merges)_
   - **Display name**: `AI Code Review`
6. Save

> The pipeline already sets `continueOnError: true` on the AI step, so a model timeout or API error will not fail the pipeline. Setting the policy to **Optional** adds a second layer of safety.

---

## Parameters reference

| Parameter           | Required | Default              | Description                                                       |
| ------------------- | -------- | -------------------- | ----------------------------------------------------------------- |
| `serviceConnection` | Yes      | -                    | ADO service connection name for Azure (used to fetch an AD token) |
| `foundryUrl`        | Yes      | -                    | Full Azure AI Foundry Responses API endpoint URL                  |
| `azureDeployment`   | No       | `gpt-5.1-codex-mini` | Model deployment name as configured in Foundry                    |
| `templateRepoAlias` | No       | `templates`          | Must match the `repository:` alias in your `resources` block      |
| `severityThreshold` | No       | `7`                  | Minimum severity (1–10) for an issue to appear in the PR comment  |
| `maxIssues`         | No       | `5`                  | Maximum number of issues posted to the PR                         |
| `pythonVersion`     | No       | `3.11`               | Python version used by the pipeline agent                         |

---

## Severity scale

| Score | Level    | Meaning                                   |
| ----- | -------- | ----------------------------------------- |
| 9–10  | Critical | Security breach, data loss, crash risk    |
| 7–8   | High     | Serious security concern, significant bug |
| 4–6   | Medium   | Code smell, minor bug, suboptimal pattern |
| 1–3   | Low      | Style, clarity (filtered out by default)  |

---

## Repository structure

```
ai-pr-review-extends.yml   # Reusable ADO pipeline template (extend this)
prompts/
  review.md                # AI review prompt with few-shot calibration examples
scripts/
  main.py                  # Entry point: orchestrates diff, AI call, and ADO posting
  azure_devops.py          # ADO API helpers: work items, PR threads, comments
  foundry.py               # Azure AI Foundry client: prompt construction and model call
```
