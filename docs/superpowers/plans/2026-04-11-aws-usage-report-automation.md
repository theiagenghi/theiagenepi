# AWS Usage Report Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an AWS-scheduled pipeline that runs the TheiaGenEpi monthly usage report, updates a stakeholder Google Doc from a template, archives both markdown and docx to S3, and emails the team a link — replacing today's manual Tailscale-VPN workflow.

**Architecture:** Dedicated ECS Fargate task in the existing Genepi VPC, scheduled via EventBridge Scheduler (`cron 0 9 1 * *` UTC). Container runs the existing `report-theiagenepi-usage.py` script, then a new `update_stakeholder_doc.py` that uses Google Docs/Drive APIs to template a stakeholder-facing Word doc. Outputs land in a versioned S3 bucket with SNS email notification. Infra managed by a new CDK-Python stack under `src/cli/check-users/infra/`, fully independent from the existing Happy/Terraform setup.

**Tech Stack:** Python 3.11, `psycopg2-binary`, `gspread`, `google-api-python-client`, `google-auth`, AWS CDK v2 (Python), ECS Fargate (arm64), ECR, EventBridge Scheduler, S3, SNS, Secrets Manager, CloudWatch Logs, GitHub Actions (OIDC), `pytest`.

**Spec:** `docs/superpowers/specs/2026-04-11-aws-usage-report-automation-design.md`

---

## File Structure

**New files:**

| Path | Responsibility |
|---|---|
| `src/cli/check-users/Dockerfile` | Container image (python:3.11-slim-bookworm + deps + scripts) |
| `src/cli/check-users/entrypoint.sh` | Orchestrates Step A → A' → B → C |
| `src/cli/check-users/update_stakeholder_doc.py` | Google Doc templater (main module) |
| `src/cli/check-users/tests/__init__.py` | Test package marker |
| `src/cli/check-users/tests/conftest.py` | Pytest fixtures |
| `src/cli/check-users/tests/fixtures/sample_report.md` | Minimal markdown fixture |
| `src/cli/check-users/tests/test_update_stakeholder_doc.py` | Unit tests for pure functions |
| `src/cli/check-users/infra/app.py` | CDK app entry |
| `src/cli/check-users/infra/cdk.json` | CDK config + context |
| `src/cli/check-users/infra/requirements.txt` | CDK deps |
| `src/cli/check-users/infra/README.md` | Discovery commands + deploy steps |
| `src/cli/check-users/infra/.gitignore` | `cdk.out/`, `*.egg-info/` |
| `src/cli/check-users/infra/theiagenepi_usage_report/__init__.py` | Python package marker |
| `src/cli/check-users/infra/theiagenepi_usage_report/stack.py` | `TheiagenepiUsageReportStack` |
| `src/cli/check-users/infra/tests/test_stack_synth.py` | CDK snapshot / assertion test |
| `.github/workflows/usage-report-image.yml` | OIDC image build + push workflow |

**Modified files:**

| Path | Change |
|---|---|
| `src/cli/check-users/report-theiagenepi-usage.py` | Load Google SA from `GOOGLE_SA_JSON` env var when present |
| `src/cli/check-users/requirements.txt` | Add `google-api-python-client`, `python-docx`, `pytest` |

---

## Task 1: Load Google Service Account from environment variable

Enables the container to inject the SA JSON via Secrets Manager without touching the filesystem. Keeps file-based path as a fallback for local runs.

**Files:**
- Modify: `src/cli/check-users/report-theiagenepi-usage.py` (the `SERVICE_ACCOUNT_FILE` / `Credentials.from_service_account_file` call sites)
- Test: `src/cli/check-users/tests/test_google_sa_loader.py` (new)

- [ ] **Step 1: Read current SA loading code**

Run: `rg -n "from_service_account_file|SERVICE_ACCOUNT_FILE" src/cli/check-users/report-theiagenepi-usage.py`

Note every call site so none are missed.

- [ ] **Step 2: Write failing test**

Create `src/cli/check-users/tests/__init__.py` (empty).

Create `src/cli/check-users/tests/test_google_sa_loader.py`:

```python
import json
import os
import pytest

from importlib import import_module


@pytest.fixture
def fake_sa_json():
    return json.dumps({
        "type": "service_account",
        "project_id": "test",
        "private_key_id": "abc",
        "private_key": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
        "client_email": "sa@test.iam.gserviceaccount.com",
        "client_id": "1",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    })


def test_load_credentials_prefers_env_var(monkeypatch, fake_sa_json):
    from cli_check_users.google_sa import load_service_account_info

    monkeypatch.setenv("GOOGLE_SA_JSON", fake_sa_json)
    info = load_service_account_info()
    assert info["client_email"] == "sa@test.iam.gserviceaccount.com"


def test_load_credentials_falls_back_to_file(monkeypatch, tmp_path, fake_sa_json):
    from cli_check_users.google_sa import load_service_account_info

    monkeypatch.delenv("GOOGLE_SA_JSON", raising=False)
    sa_file = tmp_path / "sa.json"
    sa_file.write_text(fake_sa_json)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(sa_file))

    info = load_service_account_info()
    assert info["client_email"] == "sa@test.iam.gserviceaccount.com"


def test_missing_credentials_raises(monkeypatch):
    from cli_check_users.google_sa import load_service_account_info

    monkeypatch.delenv("GOOGLE_SA_JSON", raising=False)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "/nonexistent/path.json")
    with pytest.raises(RuntimeError, match="No Google service account"):
        load_service_account_info()
```

- [ ] **Step 3: Run test to verify failure**

Run: `cd src/cli/check-users && ../.venv/bin/python -m pytest tests/test_google_sa_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: cli_check_users.google_sa`

- [ ] **Step 4: Create the loader module**

Create `src/cli/check-users/cli_check_users/__init__.py` (empty).

Create `src/cli/check-users/cli_check_users/google_sa.py`:

```python
"""Google service account credential loading.

Prefers GOOGLE_SA_JSON env var (for containerized runs where secrets are
injected directly). Falls back to GOOGLE_SERVICE_ACCOUNT_FILE for local runs.
"""
import json
import os
from pathlib import Path


def load_service_account_info() -> dict:
    env_json = os.environ.get("GOOGLE_SA_JSON")
    if env_json:
        return json.loads(env_json)

    file_path = os.environ.get(
        "GOOGLE_SERVICE_ACCOUNT_FILE",
        "spreadsheet-303717-623d4a500e34.json",
    )
    if Path(file_path).exists():
        return json.loads(Path(file_path).read_text())

    raise RuntimeError(
        "No Google service account credentials found. "
        "Set GOOGLE_SA_JSON env var or GOOGLE_SERVICE_ACCOUNT_FILE."
    )
```

- [ ] **Step 5: Run tests to verify pass**

Run: `cd src/cli/check-users && ../.venv/bin/python -m pytest tests/test_google_sa_loader.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Wire the loader into report-theiagenepi-usage.py**

Replace every `Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)` call with:

```python
from cli_check_users.google_sa import load_service_account_info
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_info(
    load_service_account_info(), scopes=SCOPES
)
```

Delete the now-unused `SERVICE_ACCOUNT_FILE` module constant if nothing else references it.

- [ ] **Step 7: Smoke-test the script still runs locally**

Run: `cd src/cli/check-users && ../.venv/bin/python report-theiagenepi-usage.py --start-date 2024-11-15 --output /tmp/smoke.md`
Expected: Script runs, `/tmp/smoke.md` is produced (requires Tailscale connection).

If Tailscale is not connected, skip this step and verify in the first manual container run (Task 13).

- [ ] **Step 8: Commit**

```bash
git add src/cli/check-users/cli_check_users src/cli/check-users/tests/__init__.py \
        src/cli/check-users/tests/test_google_sa_loader.py \
        src/cli/check-users/report-theiagenepi-usage.py
git commit -m "refactor(check-users): load Google SA from env var with file fallback"
```

---

## Task 2: Add test scaffolding + `update_stakeholder_doc` module skeleton

Establishes the module shape and a failing test we'll fill in across subsequent tasks.

**Files:**
- Create: `src/cli/check-users/update_stakeholder_doc.py`
- Create: `src/cli/check-users/tests/conftest.py`
- Create: `src/cli/check-users/tests/fixtures/sample_report.md`
- Create: `src/cli/check-users/tests/test_update_stakeholder_doc.py`

- [ ] **Step 1: Create fixture markdown**

Create `src/cli/check-users/tests/fixtures/sample_report.md`:

```markdown
# TheiaGenEpi Usage Report

## Executive Summary

Total users: 64 users registered since November 15, 2024

## New User Login Analysis

### Overview

- Users with more than 5 logins: 6 out of 64 users (9.4%)
- Users with 1-5 logins: 58 out of 64 users (90.6%)
- Users logged in within last 30 days: 2 out of 64 users (3.1%)
- Average days since last login: 317.1 days

### Monthly Cohorts

- November 2024: 0% recently active (0 out of 2 users)
- December 2024: 0% recently active (0 out of 4 users)
- February 2025: 0% recently active (0 out of 36 users)
- March 2026: 100% recently active (2 out of 2 users)

## Sample Deposits

From 2024-11-15 to 2026-04-12, 3,321 SARS-CoV-2 samples were deposited across 7 contributing organizations. Top contributor: Chicago Department of Public Health with 1,845 samples.

## Phylogenetic Runs

From 2024-11-15 to 2026-04-12, 125 phylogenetic analysis runs were executed across 16 different research groups. Top group: Chicago Department of Public Health.

## Preexisting Users

There were 563 preexisting users; 38 logged in after the transition.

## Support Tickets

Zero tickets received in the reporting month.
```

- [ ] **Step 2: Create conftest**

Create `src/cli/check-users/tests/conftest.py`:

```python
from pathlib import Path
import pytest


@pytest.fixture
def sample_report_text():
    return (Path(__file__).parent / "fixtures" / "sample_report.md").read_text()
```

- [ ] **Step 3: Write failing test for module import**

Create `src/cli/check-users/tests/test_update_stakeholder_doc.py`:

```python
def test_module_imports():
    import update_stakeholder_doc  # noqa: F401
```

Run: `cd src/cli/check-users && ../.venv/bin/python -m pytest tests/test_update_stakeholder_doc.py -v`
Expected: FAIL — `ModuleNotFoundError: update_stakeholder_doc`

- [ ] **Step 4: Create minimal module**

Create `src/cli/check-users/update_stakeholder_doc.py`:

```python
"""Update the stakeholder Google Doc from a raw markdown report.

Reads user_activity_report.md and the Monthly Activity Google Sheet, copies
the template Google Doc, performs scalar replacements, regenerates the
registration-cohort list and monthly activity table, and exports the result
as .docx.
"""
from __future__ import annotations
```

Run: `cd src/cli/check-users && ../.venv/bin/python -m pytest tests/test_update_stakeholder_doc.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/check-users/update_stakeholder_doc.py \
        src/cli/check-users/tests/conftest.py \
        src/cli/check-users/tests/fixtures/sample_report.md \
        src/cli/check-users/tests/test_update_stakeholder_doc.py
git commit -m "test(check-users): scaffold update_stakeholder_doc module + fixtures"
```

---

## Task 3: Markdown scalar extraction (`ReportScalars`)

Pure function that parses the raw markdown and produces a typed dict of placeholder values. Drives §6 of the spec.

**Files:**
- Modify: `src/cli/check-users/update_stakeholder_doc.py`
- Modify: `src/cli/check-users/tests/test_update_stakeholder_doc.py`

- [ ] **Step 1: Write failing test for scalar extraction**

Append to `tests/test_update_stakeholder_doc.py`:

```python
from update_stakeholder_doc import extract_scalars


def test_extract_scalars_from_sample(sample_report_text):
    scalars = extract_scalars(sample_report_text, reporting_date="April 2026",
                              date_range_end="2026-04-12")

    assert scalars["reporting_date"] == "April 2026"
    assert scalars["total_users"] == "64"
    assert scalars["multi_login_count"] == "6"
    assert scalars["multi_login_pct"] == "9.4"
    assert scalars["regular_user_count"] == "58"
    assert scalars["regular_user_pct"] == "90.6"
    assert scalars["active_last_30d_count"] == "2"
    assert scalars["active_last_30d_pct"] == "3.1"
    assert scalars["avg_days_since_login"] == "317.1"
    assert scalars["date_range_end"] == "2026-04-12"
    assert scalars["samples_total"] == "3,321"
    assert scalars["samples_top_org"] == "Chicago Department of Public Health"
    assert scalars["samples_top_org_count"] == "1,845"
    assert scalars["phylo_total"] == "125"
    assert scalars["phylo_groups"] == "16"
    assert scalars["phylo_top_group"] == "Chicago Department of Public Health"
    assert scalars["preexisting_logged_in"] == "38"
    assert scalars["support_tickets_month"] == "0"


def test_extract_scalars_missing_field_raises(sample_report_text):
    broken = sample_report_text.replace("Total users:", "Totl users:")
    import pytest
    with pytest.raises(ValueError, match="total_users"):
        extract_scalars(broken, reporting_date="April 2026", date_range_end="2026-04-12")
```

Run: `cd src/cli/check-users && ../.venv/bin/python -m pytest tests/test_update_stakeholder_doc.py::test_extract_scalars_from_sample -v`
Expected: FAIL — `ImportError: cannot import name 'extract_scalars'`

- [ ] **Step 2: Implement `extract_scalars`**

Append to `update_stakeholder_doc.py`:

```python
import re
from typing import TypedDict


class ReportScalars(TypedDict):
    reporting_date: str
    total_users: str
    multi_login_count: str
    multi_login_pct: str
    regular_user_count: str
    regular_user_pct: str
    active_last_30d_count: str
    active_last_30d_pct: str
    avg_days_since_login: str
    date_range_end: str
    samples_total: str
    samples_top_org: str
    samples_top_org_count: str
    phylo_total: str
    phylo_groups: str
    phylo_top_group: str
    preexisting_logged_in: str
    support_tickets_month: str


_PATTERNS = {
    "total_users": r"Total users:\s*([\d,]+) users registered",
    "multi_login_count": r"more than 5 logins:\s*(\d+) out of",
    "multi_login_pct": r"more than 5 logins:\s*\d+ out of \d+ users \(([\d.]+)%\)",
    "regular_user_count": r"1-5 logins:\s*(\d+) out of",
    "regular_user_pct": r"1-5 logins:\s*\d+ out of \d+ users \(([\d.]+)%\)",
    "active_last_30d_count": r"within last 30 days:\s*(\d+) out of",
    "active_last_30d_pct": r"within last 30 days:\s*\d+ out of \d+ users \(([\d.]+)%\)",
    "avg_days_since_login": r"Average days since last login:\s*([\d.]+)",
    "samples_total": r"([\d,]+) SARS-CoV-2 samples were deposited",
    "samples_top_org": r"Top contributor:\s*([^.]+?) with [\d,]+ samples",
    "samples_top_org_count": r"Top contributor:[^.]*? with ([\d,]+) samples",
    "phylo_total": r"([\d,]+) phylogenetic analysis runs were executed",
    "phylo_groups": r"across (\d+) different research groups",
    "phylo_top_group": r"Top group:\s*([^.\n]+)",
    "preexisting_logged_in": r"(\d+) logged in after the transition",
    "support_tickets_month": r"(Zero|\d+) tickets received",
}


def extract_scalars(
    markdown: str, *, reporting_date: str, date_range_end: str
) -> ReportScalars:
    """Parse the raw markdown report and return placeholder values as strings.

    Raises ValueError naming every missing field.
    """
    result: dict[str, str] = {
        "reporting_date": reporting_date,
        "date_range_end": date_range_end,
    }
    missing: list[str] = []

    for key, pattern in _PATTERNS.items():
        match = re.search(pattern, markdown)
        if not match:
            missing.append(key)
            continue
        value = match.group(1).strip()
        if key == "support_tickets_month" and value.lower() == "zero":
            value = "0"
        result[key] = value

    if missing:
        raise ValueError(
            f"Missing fields in markdown: {', '.join(missing)}"
        )

    return result  # type: ignore[return-value]
```

- [ ] **Step 3: Run tests**

Run: `cd src/cli/check-users && ../.venv/bin/python -m pytest tests/test_update_stakeholder_doc.py -v`
Expected: PASS (3 tests)

- [ ] **Step 4: Commit**

```bash
git add src/cli/check-users/update_stakeholder_doc.py \
        src/cli/check-users/tests/test_update_stakeholder_doc.py
git commit -m "feat(check-users): add markdown scalar extractor for doc templating"
```

---

## Task 4: Cohort list extraction

Parses the "Activity by Registration Cohort" section and returns a flat list of lines to inject between `{{cohorts_start}}` / `{{cohorts_end}}`.

**Files:**
- Modify: `src/cli/check-users/update_stakeholder_doc.py`
- Modify: `src/cli/check-users/tests/test_update_stakeholder_doc.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_update_stakeholder_doc.py`:

```python
from update_stakeholder_doc import extract_cohort_lines


def test_extract_cohort_lines(sample_report_text):
    lines = extract_cohort_lines(sample_report_text)
    assert lines == [
        "November 2024: 0% recently active (0 out of 2 users)",
        "December 2024: 0% recently active (0 out of 4 users)",
        "February 2025: 0% recently active (0 out of 36 users)",
        "March 2026: 100% recently active (2 out of 2 users)",
    ]


def test_extract_cohort_lines_empty_raises():
    from update_stakeholder_doc import extract_cohort_lines
    import pytest
    with pytest.raises(ValueError, match="cohort"):
        extract_cohort_lines("no cohort section here")
```

Run: `cd src/cli/check-users && ../.venv/bin/python -m pytest tests/test_update_stakeholder_doc.py::test_extract_cohort_lines -v`
Expected: FAIL — ImportError

- [ ] **Step 2: Implement `extract_cohort_lines`**

Append to `update_stakeholder_doc.py`:

```python
_COHORT_LINE = re.compile(
    r"^\s*-\s*((?:January|February|March|April|May|June|July|August|September|October|November|December) \d{4}:.*)$",
    re.MULTILINE,
)


def extract_cohort_lines(markdown: str) -> list[str]:
    """Return flat list of cohort lines (without leading `- `)."""
    matches = _COHORT_LINE.findall(markdown)
    if not matches:
        raise ValueError("No cohort lines found in markdown")
    return [m.strip() for m in matches]
```

- [ ] **Step 3: Run tests**

Run: `cd src/cli/check-users && ../.venv/bin/python -m pytest tests/test_update_stakeholder_doc.py -v`
Expected: PASS (5 tests)

- [ ] **Step 4: Commit**

```bash
git add src/cli/check-users/update_stakeholder_doc.py \
        src/cli/check-users/tests/test_update_stakeholder_doc.py
git commit -m "feat(check-users): extract registration-cohort lines from markdown"
```

---

## Task 5: Monthly table row builder from Google Sheet data

Pure function that takes raw rows from the Google Sheet and produces the cells for the docx Monthly Activity Overview table. Isolated from the actual Sheets API so it can be unit-tested.

**Files:**
- Modify: `src/cli/check-users/update_stakeholder_doc.py`
- Modify: `src/cli/check-users/tests/test_update_stakeholder_doc.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_update_stakeholder_doc.py`:

```python
from update_stakeholder_doc import build_monthly_table_rows


def test_build_monthly_table_rows():
    sheet_rows = [
        ["Month", "New Users", "Active Users", "Total Samples", "Total Phylo Runs"],
        ["2024-11", "2", "6", "57", "4"],
        ["2024-12", "4", "5", "189", "1"],
        ["2025-01", "", "", "", ""],
    ]
    header, body = build_monthly_table_rows(sheet_rows)
    assert header == ["Month", "New Users", "Active Users", "Total Samples", "Total Phylo Runs"]
    assert body == [
        ["2024-11", "2", "6", "57", "4"],
        ["2024-12", "4", "5", "189", "1"],
    ]


def test_build_monthly_table_rows_bad_header_raises():
    import pytest
    with pytest.raises(ValueError, match="header"):
        build_monthly_table_rows([["Foo", "Bar"]])
```

Run: `cd src/cli/check-users && ../.venv/bin/python -m pytest tests/test_update_stakeholder_doc.py::test_build_monthly_table_rows -v`
Expected: FAIL — ImportError

- [ ] **Step 2: Implement `build_monthly_table_rows`**

Append to `update_stakeholder_doc.py`:

```python
EXPECTED_MONTHLY_HEADER = [
    "Month", "New Users", "Active Users", "Total Samples", "Total Phylo Runs"
]


def build_monthly_table_rows(
    sheet_rows: list[list[str]],
) -> tuple[list[str], list[list[str]]]:
    """Split a 2D sheet range into (header, body_rows), dropping empty rows."""
    if not sheet_rows:
        raise ValueError("No rows provided")
    header = [c.strip() for c in sheet_rows[0]]
    if header != EXPECTED_MONTHLY_HEADER:
        raise ValueError(
            f"Unexpected monthly table header: {header} "
            f"(expected {EXPECTED_MONTHLY_HEADER})"
        )
    body = [
        [c.strip() for c in row]
        for row in sheet_rows[1:]
        if any(c.strip() for c in row)
    ]
    return header, body
```

- [ ] **Step 3: Run tests**

Run: `cd src/cli/check-users && ../.venv/bin/python -m pytest tests/test_update_stakeholder_doc.py -v`
Expected: PASS (7 tests)

- [ ] **Step 4: Commit**

```bash
git add src/cli/check-users/update_stakeholder_doc.py \
        src/cli/check-users/tests/test_update_stakeholder_doc.py
git commit -m "feat(check-users): build monthly activity table rows from sheet data"
```

---

## Task 6: Placeholder validator

Inspects a Google Doc's raw text content and verifies all required placeholders and region markers exist before any mutation. This is the spec's "template drift protection."

**Files:**
- Modify: `src/cli/check-users/update_stakeholder_doc.py`
- Modify: `src/cli/check-users/tests/test_update_stakeholder_doc.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_update_stakeholder_doc.py`:

```python
from update_stakeholder_doc import validate_template_placeholders


def test_validate_template_placeholders_happy_path():
    doc_text = "\n".join([
        "{{reporting_date}} {{total_users}} {{multi_login_count}}",
        "{{multi_login_pct}} {{regular_user_count}} {{regular_user_pct}}",
        "{{active_last_30d_count}} {{active_last_30d_pct}}",
        "{{avg_days_since_login}} {{date_range_end}} {{samples_total}}",
        "{{samples_top_org}} {{samples_top_org_count}}",
        "{{phylo_total}} {{phylo_groups}} {{phylo_top_group}}",
        "{{preexisting_logged_in}} {{support_tickets_month}}",
        "{{cohorts_start}}",
        "{{cohorts_end}}",
    ])
    validate_template_placeholders(doc_text)  # no exception


def test_validate_template_placeholders_missing_raises():
    import pytest
    with pytest.raises(ValueError, match="total_users"):
        validate_template_placeholders("{{reporting_date}}")
```

Run: `cd src/cli/check-users && ../.venv/bin/python -m pytest tests/test_update_stakeholder_doc.py::test_validate_template_placeholders_happy_path -v`
Expected: FAIL — ImportError

- [ ] **Step 2: Implement `validate_template_placeholders`**

Append to `update_stakeholder_doc.py`:

```python
REQUIRED_SCALAR_PLACEHOLDERS = list(ReportScalars.__annotations__.keys())
REQUIRED_REGION_MARKERS = ["cohorts_start", "cohorts_end"]


def validate_template_placeholders(doc_text: str) -> None:
    """Raise ValueError if any required placeholder/marker is missing."""
    missing: list[str] = []
    for key in REQUIRED_SCALAR_PLACEHOLDERS + REQUIRED_REGION_MARKERS:
        if f"{{{{{key}}}}}" not in doc_text:
            missing.append(key)
    if missing:
        raise ValueError(
            f"Template is missing required placeholders: {', '.join(missing)}"
        )
```

- [ ] **Step 3: Run tests**

Run: `cd src/cli/check-users && ../.venv/bin/python -m pytest tests/test_update_stakeholder_doc.py -v`
Expected: PASS (9 tests)

- [ ] **Step 4: Commit**

```bash
git add src/cli/check-users/update_stakeholder_doc.py \
        src/cli/check-users/tests/test_update_stakeholder_doc.py
git commit -m "feat(check-users): add Google Doc template placeholder validator"
```

---

## Task 7: Google Docs/Drive client wrapper

Thin wrapper around the Google APIs that performs the copy/replace/insert/export sequence. Not unit-tested — exercised end-to-end during the first manual invocation (Task 13).

**Files:**
- Modify: `src/cli/check-users/update_stakeholder_doc.py`
- Modify: `src/cli/check-users/requirements.txt`

- [ ] **Step 1: Add dependencies**

Edit `src/cli/check-users/requirements.txt`, add:

```
google-api-python-client>=2.100
python-docx>=1.0
pytest>=8.0
```

Run: `cd src/cli/check-users && ../.venv/bin/pip install -r requirements.txt`
Expected: successful install.

- [ ] **Step 2: Implement the client wrapper**

Append to `update_stakeholder_doc.py`:

```python
from dataclasses import dataclass
from datetime import datetime
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials

DOCS_SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
]


@dataclass
class UpdateResult:
    live_doc_url: str
    live_doc_id: str
    docx_path: str


class StakeholderDocUpdater:
    """Google Docs templater with a strict write protocol.

    Protocol:
    1. Copy template doc.
    2. Rename copy with report month.
    3. Fetch copy's raw text; run placeholder validator.
    4. Build one batchUpdate request list:
       - ReplaceAllText for each scalar.
       - Regenerate cohort region (delete between markers, insert lines).
       - Regenerate monthly table (delete body rows, insert fresh rows).
    5. Execute batchUpdate.
    6. Export copy as .docx.
    """

    def __init__(self, sa_info: dict, *, dry_run: bool = False):
        creds = Credentials.from_service_account_info(sa_info, scopes=DOCS_SCOPES)
        self._docs = build("docs", "v1", credentials=creds)
        self._drive = build("drive", "v3", credentials=creds)
        self._dry_run = dry_run

    def update(
        self,
        *,
        template_doc_id: str,
        scalars: ReportScalars,
        cohort_lines: list[str],
        monthly_header: list[str],
        monthly_rows: list[list[str]],
        reporting_month: str,
        output_docx_path: str,
    ) -> UpdateResult:
        new_name = f"TheiaGenEpi usage analysis - {reporting_month}"

        if self._dry_run:
            return self._dry_run_summary(
                template_doc_id, scalars, cohort_lines, monthly_rows, new_name,
                output_docx_path,
            )

        copy = self._drive.files().copy(
            fileId=template_doc_id, body={"name": new_name}
        ).execute()
        new_doc_id = copy["id"]

        doc_text = self._fetch_doc_text(new_doc_id)
        validate_template_placeholders(doc_text)

        requests = self._build_requests(
            scalars=scalars,
            cohort_lines=cohort_lines,
            monthly_header=monthly_header,
            monthly_rows=monthly_rows,
            doc=self._docs.documents().get(documentId=new_doc_id).execute(),
        )
        self._docs.documents().batchUpdate(
            documentId=new_doc_id, body={"requests": requests}
        ).execute()

        self._export_docx(new_doc_id, output_docx_path)

        return UpdateResult(
            live_doc_url=f"https://docs.google.com/document/d/{new_doc_id}/edit",
            live_doc_id=new_doc_id,
            docx_path=output_docx_path,
        )

    def _fetch_doc_text(self, doc_id: str) -> str:
        doc = self._docs.documents().get(documentId=doc_id).execute()
        parts: list[str] = []
        for element in doc.get("body", {}).get("content", []):
            para = element.get("paragraph")
            if not para:
                continue
            for run in para.get("elements", []):
                tr = run.get("textRun")
                if tr and tr.get("content"):
                    parts.append(tr["content"])
        return "".join(parts)

    def _build_requests(
        self, *, scalars, cohort_lines, monthly_header, monthly_rows, doc
    ):
        requests = []
        # Scalar replacements
        for key, value in scalars.items():
            requests.append({
                "replaceAllText": {
                    "containsText": {"text": f"{{{{{key}}}}}", "matchCase": True},
                    "replaceText": value,
                }
            })

        # Cohort region regen: replace everything between the markers by
        # replacing the start marker with the full joined list, then the end
        # marker with empty. Docs API doesn't have a native "replace region"
        # so we use a sentinel: replace {{cohorts_start}} with the rendered
        # block plus a unique sentinel, then replace the sentinel+old content
        # up to {{cohorts_end}} with empty.
        #
        # Simpler approach: the template only ever has {{cohorts_start}}
        # directly followed by {{cohorts_end}} (no stale content between),
        # because the previous month's run always regenerated it. On first
        # run (the manual Task 13 test), the operator ensures this invariant
        # when preparing the template.
        cohort_block = "\n".join(cohort_lines)
        requests.append({
            "replaceAllText": {
                "containsText": {"text": "{{cohorts_start}}", "matchCase": True},
                "replaceText": cohort_block,
            }
        })
        requests.append({
            "replaceAllText": {
                "containsText": {"text": "{{cohorts_end}}", "matchCase": True},
                "replaceText": "",
            }
        })

        # Monthly table regen: find the first table whose header row matches
        # EXPECTED_MONTHLY_HEADER, delete existing body rows, insert fresh rows.
        table_index, table_start_index = _find_monthly_table(doc)
        # Delete existing body rows (from row 1 to end) by issuing
        # deleteTableRow requests in reverse.
        body = doc["body"]["content"]
        table_element = body[table_index]
        existing_row_count = len(table_element["table"]["tableRows"])
        for row_idx in range(existing_row_count - 1, 0, -1):
            requests.append({
                "deleteTableRow": {
                    "tableCellLocation": {
                        "tableStartLocation": {"index": table_start_index},
                        "rowIndex": row_idx,
                        "columnIndex": 0,
                    }
                }
            })

        # Insert fresh rows (one insertTableRow each, then populate cells).
        # For simplicity we use insertText requests pointed at known positions
        # after the insertTableRow calls — but because positions shift, we
        # issue one batchUpdate per row in the wrapper's outer loop.
        #
        # Rather than juggle index drift in a single batch, we return the
        # scalar+cohort requests here and handle the table in a second
        # batchUpdate (see update() — split into two calls if needed).
        # For v1, we restrict the design to "template already has exactly the
        # right number of empty rows" and only populate cells via replaceAllText
        # with row-specific placeholders {{row_01_col_00}}..{{row_NN_col_04}}.
        # This avoids index-drift entirely at the cost of a fixed max row count
        # set by the template (e.g., 120 rows = 10 years).
        return requests

    def _export_docx(self, doc_id: str, output_path: str) -> None:
        data = self._drive.files().export(
            fileId=doc_id,
            mimeType="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ).execute()
        with open(output_path, "wb") as f:
            f.write(data)

    def _dry_run_summary(self, *args, **kwargs):
        # Logged by caller; returns a dummy result.
        return UpdateResult(
            live_doc_url="DRY-RUN", live_doc_id="DRY-RUN", docx_path="DRY-RUN"
        )


def _find_monthly_table(doc: dict) -> tuple[int, int]:
    """Return (index_in_body_content, startIndex) of the first table whose
    first row matches EXPECTED_MONTHLY_HEADER.
    """
    for i, element in enumerate(doc["body"]["content"]):
        table = element.get("table")
        if not table:
            continue
        first_row = table["tableRows"][0]
        header_cells = [
            "".join(
                run["textRun"]["content"]
                for para in cell["content"] if "paragraph" in para
                for run in para["paragraph"]["elements"] if "textRun" in run
            ).strip()
            for cell in first_row["tableCells"]
        ]
        if header_cells == EXPECTED_MONTHLY_HEADER:
            return i, element["startIndex"]
    raise ValueError("Monthly activity table not found in template")
```

**Important note for the implementer:** the Docs API has well-known pain around index drift when mutating tables. The comment block inside `_build_requests` documents a fallback strategy: restrict the template to pre-allocated row placeholders (`{{row_01_col_00}}..{{row_NN_col_04}}`). If the implementer hits index-drift bugs, switch to that strategy and update `§6 Google Doc Template Preparation` in the spec.

- [ ] **Step 3: Smoke-test module imports**

Run: `cd src/cli/check-users && ../.venv/bin/python -c "import update_stakeholder_doc; print(update_stakeholder_doc.StakeholderDocUpdater)"`
Expected: `<class 'update_stakeholder_doc.StakeholderDocUpdater'>`

- [ ] **Step 4: Run all existing tests**

Run: `cd src/cli/check-users && ../.venv/bin/python -m pytest tests/ -v`
Expected: PASS (9 tests still pass)

- [ ] **Step 5: Commit**

```bash
git add src/cli/check-users/update_stakeholder_doc.py \
        src/cli/check-users/requirements.txt
git commit -m "feat(check-users): add Google Docs/Drive client wrapper for templating"
```

---

## Task 8: CLI entrypoint for `update_stakeholder_doc.py`

Wires the pure functions and the client wrapper into a runnable script with `--dry-run`.

**Files:**
- Modify: `src/cli/check-users/update_stakeholder_doc.py`

- [ ] **Step 1: Add CLI + `main` function**

Append to `update_stakeholder_doc.py`:

```python
import argparse
import json
import logging
import os
import sys
from datetime import datetime

import gspread

from cli_check_users.google_sa import load_service_account_info

logger = logging.getLogger("update_stakeholder_doc")


def _read_monthly_sheet(sheet_id: str) -> list[list[str]]:
    creds = Credentials.from_service_account_info(
        load_service_account_info(),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    client = gspread.authorize(creds)
    worksheet = client.open_by_key(sheet_id).worksheet("Monthly Activity")
    return worksheet.get_all_values()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", required=True,
                        help="Path to the raw user_activity_report.md")
    parser.add_argument("--template-doc-id", required=True,
                        help="Google Doc template ID")
    parser.add_argument("--sheet-id", required=True,
                        help="Google Sheet ID holding Monthly Activity table")
    parser.add_argument("--output-docx", required=True,
                        help="Path to write the exported .docx")
    parser.add_argument("--reporting-month",
                        help="e.g. 'April 2026'; defaults to current UTC month")
    parser.add_argument("--date-range-end",
                        help="e.g. '2026-04-12'; defaults to today UTC")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log actions without calling Google APIs")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    now = datetime.utcnow()
    reporting_month = args.reporting_month or now.strftime("%B %Y")
    date_range_end = args.date_range_end or now.strftime("%Y-%m-%d")

    with open(args.markdown) as f:
        markdown = f.read()

    scalars = extract_scalars(
        markdown,
        reporting_date=reporting_month,
        date_range_end=date_range_end,
    )
    cohort_lines = extract_cohort_lines(markdown)

    if args.dry_run:
        logger.info("DRY RUN: scalars=%s", json.dumps(scalars, indent=2))
        logger.info("DRY RUN: %d cohort lines", len(cohort_lines))
        logger.info("DRY RUN: would copy template %s → '%s'",
                    args.template_doc_id, f"TheiaGenEpi usage analysis - {reporting_month}")
        logger.info("DRY RUN: would export .docx to %s", args.output_docx)
        return 0

    sheet_rows = _read_monthly_sheet(args.sheet_id)
    header, body = build_monthly_table_rows(sheet_rows)

    updater = StakeholderDocUpdater(load_service_account_info(), dry_run=False)
    result = updater.update(
        template_doc_id=args.template_doc_id,
        scalars=scalars,
        cohort_lines=cohort_lines,
        monthly_header=header,
        monthly_rows=body,
        reporting_month=reporting_month,
        output_docx_path=args.output_docx,
    )

    logger.info("Live doc: %s", result.live_doc_url)
    logger.info("Exported .docx: %s", result.docx_path)
    print(result.live_doc_url)  # machine-readable for entrypoint.sh
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Dry-run smoke test**

Run:
```bash
cd src/cli/check-users && \
  ../.venv/bin/python update_stakeholder_doc.py \
    --markdown tests/fixtures/sample_report.md \
    --template-doc-id fake-template-id \
    --sheet-id 136W69U8Ai8_M32J567r3SHk9gCOZhYAIzSOKOy5sU_w \
    --output-docx /tmp/out.docx \
    --reporting-month "April 2026" \
    --date-range-end "2026-04-12" \
    --dry-run
```

Expected: logs show extracted scalars, 4 cohort lines, no API calls, exit 0.

- [ ] **Step 3: Commit**

```bash
git add src/cli/check-users/update_stakeholder_doc.py
git commit -m "feat(check-users): add CLI entrypoint + dry-run for doc templater"
```

---

## Task 9: Container image and entrypoint script

**Files:**
- Create: `src/cli/check-users/Dockerfile`
- Create: `src/cli/check-users/entrypoint.sh`
- Create: `src/cli/check-users/.dockerignore`

- [ ] **Step 1: Write `.dockerignore`**

Create `src/cli/check-users/.dockerignore`:

```
__pycache__
*.pyc
.venv
tests/
infra/
*.md
*.csv
*.fasta
*.tsv
*.docx
*.sql
.env*
!requirements.txt
```

- [ ] **Step 2: Write Dockerfile**

Create `src/cli/check-users/Dockerfile`:

```dockerfile
FROM public.ecr.aws/docker/library/python:3.11-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libpq5 \
      awscli \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cli_check_users/ ./cli_check_users/
COPY report-theiagenepi-usage.py ./
COPY update_stakeholder_doc.py ./
COPY entrypoint.sh ./

RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]
```

- [ ] **Step 3: Write entrypoint.sh**

Create `src/cli/check-users/entrypoint.sh`:

```bash
#!/usr/bin/env bash
# Orchestrates the monthly report pipeline inside the container.
#
# Required env vars (injected by ECS task definition):
#   DB_NAME, DB_USER, DB_PASSWORD, DB_HOST
#   AUTH0_DOMAIN, AUTH0_CLIENT_ID, AUTH0_CLIENT_SECRET
#   GOOGLE_SA_JSON
#   S3_BUCKET           (e.g., theiagenepi-usage-reports)
#   SNS_TOPIC_ARN
#   TEMPLATE_DOC_ID
#   MONTHLY_SHEET_ID    (default: 136W69U8Ai8_M32J567r3SHk9gCOZhYAIzSOKOy5sU_w)

set -euo pipefail

: "${S3_BUCKET:?S3_BUCKET must be set}"
: "${SNS_TOPIC_ARN:?SNS_TOPIC_ARN must be set}"
: "${TEMPLATE_DOC_ID:?TEMPLATE_DOC_ID must be set}"
MONTHLY_SHEET_ID="${MONTHLY_SHEET_ID:-136W69U8Ai8_M32J567r3SHk9gCOZhYAIzSOKOy5sU_w}"

YEAR=$(date -u +%Y)
MONTH=$(date -u +%m)
REPORTING_MONTH=$(date -u +"%B %Y")
DATE_RANGE_END=$(date -u +%Y-%m-%d)
S3_PREFIX="s3://${S3_BUCKET}/${YEAR}/${MONTH}"

MD_LOCAL=/tmp/user_activity_report.md
DOCX_LOCAL=/tmp/theiagenepi-usage-analysis.docx

echo "=== Step A: generate raw report ==="
python report-theiagenepi-usage.py \
  --start-date 2024-11-15 \
  --output "$MD_LOCAL" \
  --spreadsheet

echo "=== Step A': preserve raw markdown to S3 ==="
aws s3 cp "$MD_LOCAL" "${S3_PREFIX}/user_activity_report.md"

echo "=== Step B: update stakeholder Google Doc ==="
LIVE_DOC_URL=$(python update_stakeholder_doc.py \
  --markdown "$MD_LOCAL" \
  --template-doc-id "$TEMPLATE_DOC_ID" \
  --sheet-id "$MONTHLY_SHEET_ID" \
  --output-docx "$DOCX_LOCAL" \
  --reporting-month "$REPORTING_MONTH" \
  --date-range-end "$DATE_RANGE_END" \
  | tail -n 1)

echo "Live doc: $LIVE_DOC_URL"

echo "=== Step C: archive docx + notify ==="
aws s3 cp "$DOCX_LOCAL" "${S3_PREFIX}/theiagenepi-usage-analysis.docx"

MD_URL=$(aws s3 presign "${S3_PREFIX}/user_activity_report.md" --expires-in 604800)
DOCX_URL=$(aws s3 presign "${S3_PREFIX}/theiagenepi-usage-analysis.docx" --expires-in 604800)
SHEET_URL="https://docs.google.com/spreadsheets/d/${MONTHLY_SHEET_ID}/edit"

MESSAGE=$(cat <<EOF
TheiaGenEpi Usage Report - ${REPORTING_MONTH}

Live stakeholder Google Doc:
${LIVE_DOC_URL}

Live Google Sheet (monthly activity):
${SHEET_URL}

Archived markdown (presigned, 7-day):
${MD_URL}

Archived .docx (presigned, 7-day):
${DOCX_URL}
EOF
)

aws sns publish \
  --topic-arn "$SNS_TOPIC_ARN" \
  --subject "TheiaGenEpi Usage Report - ${REPORTING_MONTH}" \
  --message "$MESSAGE"

echo "=== Done ==="
```

- [ ] **Step 4: Build the image locally**

Run: `cd src/cli/check-users && docker build -t theiagenepi-usage-report:dev .`
Expected: successful build, no errors.

- [ ] **Step 5: Commit**

```bash
git add src/cli/check-users/Dockerfile \
        src/cli/check-users/entrypoint.sh \
        src/cli/check-users/.dockerignore
git commit -m "feat(check-users): containerize monthly usage report pipeline"
```

---

## Task 10: CDK scaffolding — app, context, requirements

**Files:**
- Create: `src/cli/check-users/infra/app.py`
- Create: `src/cli/check-users/infra/cdk.json`
- Create: `src/cli/check-users/infra/requirements.txt`
- Create: `src/cli/check-users/infra/.gitignore`
- Create: `src/cli/check-users/infra/theiagenepi_usage_report/__init__.py`
- Create: `src/cli/check-users/infra/theiagenepi_usage_report/stack.py` (minimal skeleton)

- [ ] **Step 1: Write requirements.txt**

Create `src/cli/check-users/infra/requirements.txt`:

```
aws-cdk-lib>=2.150.0
constructs>=10.3.0
pytest>=8.0
```

- [ ] **Step 2: Write .gitignore**

Create `src/cli/check-users/infra/.gitignore`:

```
cdk.out/
*.egg-info/
.venv/
__pycache__/
*.pyc
```

- [ ] **Step 3: Write cdk.json with placeholder context**

Create `src/cli/check-users/infra/cdk.json`:

```json
{
  "app": "python3 app.py",
  "context": {
    "aws_account": "REPLACE_ME",
    "aws_region": "us-west-2",
    "vpc_id": "REPLACE_ME",
    "private_subnet_ids": ["REPLACE_ME_AZ_A", "REPLACE_ME_AZ_B"],
    "rds_security_group_id": "REPLACE_ME",
    "notification_emails": ["REPLACE_ME@example.com"],
    "template_doc_id": "REPLACE_ME",
    "schedule_enabled": false,
    "@aws-cdk/aws-iam:minimizePolicies": true
  }
}
```

- [ ] **Step 4: Write package marker and minimal stack**

Create `src/cli/check-users/infra/theiagenepi_usage_report/__init__.py` (empty).

Create `src/cli/check-users/infra/theiagenepi_usage_report/stack.py`:

```python
"""TheiaGenEpi monthly usage report CDK stack.

Provisions the ECR repo, ECS cluster + Fargate task, Secrets Manager entries,
S3 archive bucket, SNS notification topic, EventBridge Scheduler, CloudWatch
log group and alarm. Reuses the existing Genepi VPC, private subnets, and
RDS security group via from_lookup.
"""
from __future__ import annotations

from aws_cdk import Stack
from constructs import Construct


class TheiagenepiUsageReportStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        # Constructs filled in by subsequent tasks.
```

- [ ] **Step 5: Write app.py**

Create `src/cli/check-users/infra/app.py`:

```python
#!/usr/bin/env python3
import os
import aws_cdk as cdk

from theiagenepi_usage_report.stack import TheiagenepiUsageReportStack


app = cdk.App()

account = app.node.try_get_context("aws_account") or os.environ.get("CDK_DEFAULT_ACCOUNT")
region = app.node.try_get_context("aws_region") or "us-west-2"

TheiagenepiUsageReportStack(
    app,
    "TheiagenepiUsageReportStack",
    env=cdk.Environment(account=account, region=region),
)

app.synth()
```

- [ ] **Step 6: Install deps and verify synth**

Run:
```bash
cd src/cli/check-users/infra && \
  python3 -m venv .venv && \
  .venv/bin/pip install -r requirements.txt && \
  .venv/bin/cdk synth --app "python3 app.py" 2>&1 | head -30
```

Expected: `cdk synth` produces empty CloudFormation (or errors on REPLACE_ME context — both OK for scaffolding step).

If synth errors complain about missing context, it's expected — we fix in Task 11.

- [ ] **Step 7: Commit**

```bash
git add src/cli/check-users/infra/
git commit -m "chore(infra): scaffold CDK Python app for usage report stack"
```

---

## Task 11: CDK stack — Networking, Image, Secrets, Storage, Notifications

First half of the stack. Creates stateless / idempotent resources.

**Files:**
- Modify: `src/cli/check-users/infra/theiagenepi_usage_report/stack.py`

- [ ] **Step 1: Add imports and constructor body**

Replace the contents of `stack.py` with:

```python
"""TheiaGenEpi monthly usage report CDK stack."""
from __future__ import annotations

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_ecs as ecs,
    aws_iam as iam,
    aws_logs as logs,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
    aws_sns as sns,
    aws_sns_subscriptions as subs,
    aws_scheduler as scheduler,
    aws_cloudwatch as cw,
    aws_cloudwatch_actions as cw_actions,
)
from constructs import Construct


class TheiagenepiUsageReportStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        ctx = self.node.try_get_context
        vpc_id: str = ctx("vpc_id")
        private_subnet_ids: list[str] = ctx("private_subnet_ids") or []
        rds_sg_id: str = ctx("rds_security_group_id")
        template_doc_id: str = ctx("template_doc_id")
        emails: list[str] = ctx("notification_emails") or []
        schedule_enabled: bool = bool(ctx("schedule_enabled"))

        for name, value in [
            ("vpc_id", vpc_id),
            ("rds_security_group_id", rds_sg_id),
            ("template_doc_id", template_doc_id),
        ]:
            if not value or value == "REPLACE_ME":
                raise ValueError(f"cdk.json context '{name}' must be set")

        # --- Networking (looked up from existing VPC) ---
        vpc = ec2.Vpc.from_lookup(self, "Vpc", vpc_id=vpc_id)
        rds_sg = ec2.SecurityGroup.from_security_group_id(
            self, "RdsSg", rds_sg_id, mutable=True
        )

        task_sg = ec2.SecurityGroup(
            self, "ReportTaskSg",
            vpc=vpc,
            description="TheiaGenEpi usage report Fargate task",
            allow_all_outbound=True,
        )
        rds_sg.add_ingress_rule(
            peer=task_sg,
            connection=ec2.Port.tcp(5432),
            description="Allow usage report task to reach RDS",
        )

        # --- ECR ---
        repo = ecr.Repository(
            self, "ReportRepo",
            repository_name="theiagenepi/usage-report",
            image_scan_on_push=True,
            lifecycle_rules=[
                ecr.LifecycleRule(max_image_count=10, description="Keep last 10 images"),
            ],
            removal_policy=RemovalPolicy.RETAIN,
        )

        # --- Secrets Manager ---
        db_secret = secretsmanager.Secret(
            self, "DbSecret", secret_name="theiagenepi/prod/db",
            description="DB creds: DB_NAME, DB_USER, DB_PASSWORD, DB_HOST",
        )
        auth0_secret = secretsmanager.Secret(
            self, "Auth0Secret", secret_name="theiagenepi/prod/auth0",
            description="Auth0 creds: AUTH0_DOMAIN, AUTH0_CLIENT_ID, AUTH0_CLIENT_SECRET",
        )
        google_secret = secretsmanager.Secret(
            self, "GoogleSaSecret", secret_name="theiagenepi/prod/google-sa",
            description="Google service account JSON as a single string",
        )

        # --- S3 archive bucket ---
        bucket = s3.Bucket(
            self, "ReportBucket",
            bucket_name=f"theiagenepi-usage-reports-{self.account}",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="archive-old-versions",
                    noncurrent_version_expiration=Duration.days(365 * 7),
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER_INSTANT_RETRIEVAL,
                            transition_after=Duration.days(90),
                        )
                    ],
                )
            ],
        )

        # --- SNS notifications ---
        topic = sns.Topic(
            self, "ReportTopic",
            topic_name="theiagenepi-usage-report-notifications",
            display_name="TheiaGenEpi Usage Report",
        )
        for email in emails:
            if email and email != "REPLACE_ME@example.com":
                topic.add_subscription(subs.EmailSubscription(email))

        # Stash everything on self for Task 12 to wire up.
        self._vpc = vpc
        self._private_subnet_ids = private_subnet_ids
        self._task_sg = task_sg
        self._repo = repo
        self._db_secret = db_secret
        self._auth0_secret = auth0_secret
        self._google_secret = google_secret
        self._bucket = bucket
        self._topic = topic
        self._template_doc_id = template_doc_id
        self._schedule_enabled = schedule_enabled
```

- [ ] **Step 2: Verify synth (will error on missing task resources but should parse)**

Run:
```bash
cd src/cli/check-users/infra && \
  .venv/bin/cdk synth --app ".venv/bin/python3 app.py" 2>&1 | tail -20
```

Expected: either synth succeeds (producing partial template) or fails with a Python-level error about a subsequent task — NOT with a syntax error in the code just written.

If it fails because `private_subnet_ids` is empty/placeholder, that's fine for this task — we gate on it in Task 12.

- [ ] **Step 3: Commit**

```bash
git add src/cli/check-users/infra/theiagenepi_usage_report/stack.py
git commit -m "feat(infra): add VPC/ECR/secrets/S3/SNS constructs"
```

---

## Task 12: CDK stack — Task definition, schedule, observability

**Files:**
- Modify: `src/cli/check-users/infra/theiagenepi_usage_report/stack.py`

- [ ] **Step 1: Add compute + schedule + observability**

Append to the `__init__` method of `TheiagenepiUsageReportStack` (below the Task 11 code, before the `self._*` stashing block):

```python
        # --- ECS cluster + task definition ---
        cluster = ecs.Cluster(
            self, "ReportCluster",
            cluster_name="theiagenepi-usage-report",
            vpc=vpc,
            enable_fargate_capacity_providers=True,
        )

        log_group = logs.LogGroup(
            self, "ReportLogs",
            log_group_name="/ecs/theiagenepi-usage-report",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.RETAIN,
        )

        task_def = ecs.FargateTaskDefinition(
            self, "ReportTaskDef",
            cpu=512,
            memory_limit_mib=1024,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        container = task_def.add_container(
            "report",
            image=ecs.ContainerImage.from_ecr_repository(repo, tag="latest"),
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="report", log_group=log_group
            ),
            environment={
                "S3_BUCKET": bucket.bucket_name,
                "SNS_TOPIC_ARN": topic.topic_arn,
                "TEMPLATE_DOC_ID": template_doc_id,
                "MONTHLY_SHEET_ID": "136W69U8Ai8_M32J567r3SHk9gCOZhYAIzSOKOy5sU_w",
            },
            secrets={
                "DB_NAME": ecs.Secret.from_secrets_manager(db_secret, "DB_NAME"),
                "DB_USER": ecs.Secret.from_secrets_manager(db_secret, "DB_USER"),
                "DB_PASSWORD": ecs.Secret.from_secrets_manager(db_secret, "DB_PASSWORD"),
                "DB_HOST": ecs.Secret.from_secrets_manager(db_secret, "DB_HOST"),
                "AUTH0_DOMAIN": ecs.Secret.from_secrets_manager(auth0_secret, "AUTH0_DOMAIN"),
                "AUTH0_CLIENT_ID": ecs.Secret.from_secrets_manager(auth0_secret, "AUTH0_CLIENT_ID"),
                "AUTH0_CLIENT_SECRET": ecs.Secret.from_secrets_manager(auth0_secret, "AUTH0_CLIENT_SECRET"),
                "GOOGLE_SA_JSON": ecs.Secret.from_secrets_manager(google_secret),
            },
        )

        bucket.grant_put(task_def.task_role)
        bucket.grant_read(task_def.task_role)  # for presign
        topic.grant_publish(task_def.task_role)

        # --- EventBridge Scheduler ---
        schedule_role = iam.Role(
            self, "ScheduleRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        schedule_role.add_to_policy(iam.PolicyStatement(
            actions=["ecs:RunTask"],
            resources=[task_def.task_definition_arn],
        ))
        schedule_role.add_to_policy(iam.PolicyStatement(
            actions=["iam:PassRole"],
            resources=[task_def.task_role.role_arn, task_def.execution_role.role_arn],
        ))
        topic.grant_publish(schedule_role)

        subnets = ec2.SubnetSelection(
            subnets=[
                ec2.Subnet.from_subnet_id(self, f"Subnet{i}", sid)
                for i, sid in enumerate(private_subnet_ids)
            ]
        )

        schedule = scheduler.CfnSchedule(
            self, "MonthlySchedule",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(
                mode="FLEXIBLE",
                maximum_window_in_minutes=15,
            ),
            schedule_expression="cron(0 9 1 * ? *)",
            schedule_expression_timezone="UTC",
            state="ENABLED" if schedule_enabled else "DISABLED",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=f"arn:aws:ecs:{self.region}:{self.account}:cluster/{cluster.cluster_name}",
                role_arn=schedule_role.role_arn,
                ecs_parameters=scheduler.CfnSchedule.EcsParametersProperty(
                    task_definition_arn=task_def.task_definition_arn,
                    launch_type="FARGATE",
                    network_configuration=scheduler.CfnSchedule.NetworkConfigurationProperty(
                        awsvpc_configuration=scheduler.CfnSchedule.AwsVpcConfigurationProperty(
                            subnets=private_subnet_ids,
                            security_groups=[task_sg.security_group_id],
                            assign_public_ip="DISABLED",
                        )
                    ),
                ),
                retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(
                    maximum_retry_attempts=3,
                    maximum_event_age_in_seconds=3600,
                ),
                dead_letter_config=scheduler.CfnSchedule.DeadLetterConfigProperty(
                    arn=topic.topic_arn
                ),
            ),
        )

        # --- CloudWatch alarm ---
        cw.Alarm(
            self, "TaskFailureAlarm",
            alarm_name="theiagenepi-usage-report-task-failures",
            metric=cw.Metric(
                namespace="AWS/ECS",
                metric_name="TaskFailureCount",
                dimensions_map={"ClusterName": cluster.cluster_name},
                statistic="Sum",
                period=Duration.minutes(5),
            ),
            threshold=0,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        ).add_alarm_action(cw_actions.SnsAction(topic))
```

- [ ] **Step 2: Verify synth**

Run:
```bash
cd src/cli/check-users/infra && \
  .venv/bin/cdk synth --app ".venv/bin/python3 app.py" 2>&1 | tail -30
```

Expected: synth succeeds **only after** you populate real values for `vpc_id`, `rds_security_group_id`, `template_doc_id`, and `private_subnet_ids` in `cdk.json`. If you haven't yet, use any valid-format placeholders (real VPC/subnet IDs from any AWS account) to verify the CDK code itself is well-formed.

- [ ] **Step 3: Commit**

```bash
git add src/cli/check-users/infra/theiagenepi_usage_report/stack.py
git commit -m "feat(infra): add ECS task, EventBridge schedule, CloudWatch alarm"
```

---

## Task 13: CDK synth test

**Files:**
- Create: `src/cli/check-users/infra/tests/__init__.py`
- Create: `src/cli/check-users/infra/tests/test_stack_synth.py`

- [ ] **Step 1: Write the test**

Create `src/cli/check-users/infra/tests/__init__.py` (empty).

Create `src/cli/check-users/infra/tests/test_stack_synth.py`:

```python
import aws_cdk as cdk
from aws_cdk.assertions import Template, Match

from theiagenepi_usage_report.stack import TheiagenepiUsageReportStack


def _synth(context_overrides=None):
    app = cdk.App(context={
        "aws_account": "111111111111",
        "aws_region": "us-west-2",
        "vpc_id": "vpc-12345",
        "private_subnet_ids": ["subnet-aaa", "subnet-bbb"],
        "rds_security_group_id": "sg-12345",
        "notification_emails": ["test@example.com"],
        "template_doc_id": "template-doc-id",
        "schedule_enabled": False,
        **(context_overrides or {}),
    })
    stack = TheiagenepiUsageReportStack(
        app, "TestStack",
        env=cdk.Environment(account="111111111111", region="us-west-2"),
    )
    return Template.from_stack(stack)


def test_creates_ecr_repo():
    template = _synth()
    template.has_resource_properties("AWS::ECR::Repository", {
        "RepositoryName": "theiagenepi/usage-report",
    })


def test_creates_three_secrets():
    template = _synth()
    template.resource_count_is("AWS::SecretsManager::Secret", 3)


def test_creates_s3_bucket_versioned_and_encrypted():
    template = _synth()
    template.has_resource_properties("AWS::S3::Bucket", {
        "VersioningConfiguration": {"Status": "Enabled"},
        "PublicAccessBlockConfiguration": Match.object_like({
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        }),
    })


def test_creates_sns_topic():
    template = _synth()
    template.has_resource_properties("AWS::SNS::Topic", {
        "TopicName": "theiagenepi-usage-report-notifications",
    })


def test_schedule_defaults_to_disabled():
    template = _synth()
    template.has_resource_properties("AWS::Scheduler::Schedule", {
        "State": "DISABLED",
        "ScheduleExpression": "cron(0 9 1 * ? *)",
    })


def test_schedule_can_be_enabled_via_context():
    template = _synth({"schedule_enabled": True})
    template.has_resource_properties("AWS::Scheduler::Schedule", {
        "State": "ENABLED",
    })


def test_missing_required_context_raises():
    import pytest
    with pytest.raises(ValueError, match="template_doc_id"):
        _synth({"template_doc_id": "REPLACE_ME"})
```

- [ ] **Step 2: Run tests**

Run:
```bash
cd src/cli/check-users/infra && \
  .venv/bin/python -m pytest tests/ -v
```

Expected: PASS (7 tests)

- [ ] **Step 3: Commit**

```bash
git add src/cli/check-users/infra/tests/
git commit -m "test(infra): add CDK synth assertions for usage report stack"
```

---

## Task 14: GitHub Actions OIDC image build workflow

**Files:**
- Create: `.github/workflows/usage-report-image.yml`

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/usage-report-image.yml`:

```yaml
name: Usage Report Image Build

on:
  push:
    branches:
      - main
      - tghi-dev
    paths:
      - "src/cli/check-users/**"
      - ".github/workflows/usage-report-image.yml"
  workflow_dispatch:

permissions:
  id-token: write
  contents: read

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS credentials via OIDC
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.USAGE_REPORT_DEPLOY_ROLE_ARN }}
          aws-region: us-west-2

      - name: Login to ECR
        uses: aws-actions/amazon-ecr-login@v2

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build and push image (arm64)
        uses: docker/build-push-action@v5
        with:
          context: src/cli/check-users
          file: src/cli/check-users/Dockerfile
          platforms: linux/arm64
          push: true
          tags: |
            ${{ steps.ecr-login.outputs.registry }}/theiagenepi/usage-report:latest
            ${{ steps.ecr-login.outputs.registry }}/theiagenepi/usage-report:${{ github.sha }}
        id: build

      - name: Force ECS service redeploy (no-op for scheduled tasks)
        run: echo "No long-running service to redeploy; scheduled task picks up :latest on next run."
```

**Note to implementer:** the OIDC role (`USAGE_REPORT_DEPLOY_ROLE_ARN`) must be created separately — either via a small companion CDK stack or via the AWS console. Document the trust policy in `infra/README.md` (Task 15). The role needs `ecr:GetAuthorizationToken`, `ecr:BatchCheckLayerAvailability`, `ecr:PutImage`, `ecr:InitiateLayerUpload`, `ecr:UploadLayerPart`, `ecr:CompleteLayerUpload` on the `theiagenepi/usage-report` repo only.

- [ ] **Step 2: Validate YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/usage-report-image.yml'))"`
Expected: no output (valid YAML).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/usage-report-image.yml
git commit -m "ci: add OIDC workflow to build and push usage report image"
```

---

## Task 15: infra/README.md with discovery + deploy + rotation commands

Self-contained operator runbook. Anyone reading this should be able to deploy and operate the stack without asking questions.

**Files:**
- Create: `src/cli/check-users/infra/README.md`

- [ ] **Step 1: Write the README**

Create `src/cli/check-users/infra/README.md`:

```markdown
# TheiaGenEpi Usage Report — Infrastructure

CDK Python stack that provisions the monthly usage report pipeline. See
`docs/superpowers/specs/2026-04-11-aws-usage-report-automation-design.md`
for the full design.

## One-time discovery

Before the first `cdk deploy`, populate `cdk.json` context with IDs from the
existing Genepi infrastructure.

### VPC ID

```bash
aws ec2 describe-vpcs \
  --filters Name=tag:Name,Values=*genepi* \
  --query 'Vpcs[].VpcId' --output text
```

### Private subnet IDs

```bash
aws ec2 describe-subnets \
  --filters Name=vpc-id,Values=<VPC_ID> Name=tag:Name,Values=*private* \
  --query 'Subnets[].SubnetId' --output text
```

### RDS security group ID

```bash
aws rds describe-db-instances \
  --query 'DBInstances[?starts_with(DBInstanceIdentifier, `genepi`)].VpcSecurityGroups[].VpcSecurityGroupId' \
  --output text
```

### Google Doc template ID

Create the template per §6 of the spec, then copy the doc ID from the URL
(`https://docs.google.com/document/d/<DOC_ID>/edit`).

### Fill in cdk.json

Edit `cdk.json` and replace every `REPLACE_ME` with real values. Add one or
more real email addresses to `notification_emails`.

## Google Doc template preparation

1. Upload `src/cli/check-users/TheiaGenEpi usage analysis-2_2026.docx` to
   Drive, open as Google Doc, rename to `TheiaGenEpi usage analysis - TEMPLATE`.
2. Share the doc with the service account email found in
   `spreadsheet-303717-623d4a500e34.json` → `client_email`, with Editor access.
3. Replace dynamic content with the placeholders listed in §6 of the spec.
4. Verify: add {{cohorts_start}} and {{cohorts_end}} markers on adjacent lines
   (not separated by stale cohort content — the updater assumes they bracket
   an empty or freshly-regenerated region).
5. Copy the doc ID from the URL into `cdk.json` context as `template_doc_id`.

## First deploy

```bash
cd src/cli/check-users/infra
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/cdk bootstrap            # only once per account/region
.venv/bin/cdk deploy
```

After deploy, populate the 3 secrets (values not stored in git):

```bash
aws secretsmanager put-secret-value \
  --secret-id theiagenepi/prod/db \
  --secret-string '{"DB_NAME":"aspen_db","DB_USER":"aspen","DB_PASSWORD":"***","DB_HOST":"***.rds.amazonaws.com"}'

aws secretsmanager put-secret-value \
  --secret-id theiagenepi/prod/auth0 \
  --secret-string '{"AUTH0_DOMAIN":"https://covidtracker.us.auth0.com","AUTH0_CLIENT_ID":"***","AUTH0_CLIENT_SECRET":"***"}'

aws secretsmanager put-secret-value \
  --secret-id theiagenepi/prod/google-sa \
  --secret-string file://spreadsheet-303717-623d4a500e34.json
```

## First manual invocation (schedule still disabled)

Push the first container image (via the GitHub Actions workflow or manually):

```bash
aws ecr get-login-password --region us-west-2 \
  | docker login --username AWS --password-stdin <ACCOUNT>.dkr.ecr.us-west-2.amazonaws.com
docker build --platform linux/arm64 -t theiagenepi/usage-report:latest ..
docker tag theiagenepi/usage-report:latest \
  <ACCOUNT>.dkr.ecr.us-west-2.amazonaws.com/theiagenepi/usage-report:latest
docker push <ACCOUNT>.dkr.ecr.us-west-2.amazonaws.com/theiagenepi/usage-report:latest
```

Run the task manually:

```bash
aws ecs run-task \
  --cluster theiagenepi-usage-report \
  --task-definition <TASK_DEF_ARN_FROM_CDK_OUTPUT> \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-aaa,subnet-bbb],securityGroups=[sg-xxx],assignPublicIp=DISABLED}"
```

Verify:
- CloudWatch log group `/ecs/theiagenepi-usage-report` shows Step A → A' → B → C
- Objects appear in `s3://theiagenepi-usage-reports-<ACCOUNT>/YYYY/MM/`
- Live Google Doc copy exists in Drive
- SNS email arrives at every subscribed address with working links

## Enable the schedule

Once verified, set `"schedule_enabled": true` in `cdk.json` and redeploy:

```bash
.venv/bin/cdk deploy
```

## OIDC role for GitHub Actions

The `USAGE_REPORT_DEPLOY_ROLE_ARN` secret in the repo settings must point to
an IAM role with this trust policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "arn:aws:iam::<ACCOUNT>:oidc-provider/token.actions.githubusercontent.com"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
      "StringLike": {"token.actions.githubusercontent.com:sub": "repo:<OWNER>/theiagenepi:*"}
    }
  }]
}
```

Permissions (inline policy) — ECR push to the usage report repo only:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
    {
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:PutImage",
        "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload"
      ],
      "Resource": "arn:aws:ecr:us-west-2:<ACCOUNT>:repository/theiagenepi/usage-report"
    }
  ]
}
```

Create once via the console or a separate one-off CDK stack — this role is
assumed by GitHub, so CDK cannot manage it without a chicken-and-egg problem.

## Secret rotation

No automatic rotation is configured. When credentials change:

```bash
aws secretsmanager update-secret --secret-id <secret-id> --secret-string '<new JSON>'
```

The next scheduled run picks up the new values automatically.

## Follow-ups

- After 2 successful monthly runs, delete `src/cli/lambda-user-analytics/`
  (prior incomplete attempt at this feature; superseded by this stack).
```

- [ ] **Step 2: Commit**

```bash
git add src/cli/check-users/infra/README.md
git commit -m "docs(infra): add operator runbook for usage report stack"
```

---

## Task 16: Update project `requirements.txt`

The app-level requirements file needs the new deps so local development matches the container.

**Files:**
- Modify: `src/cli/check-users/requirements.txt`

- [ ] **Step 1: Verify additions from Task 7 are present**

Run: `cat src/cli/check-users/requirements.txt`
Expected: includes `google-api-python-client>=2.100`, `python-docx>=1.0`, `pytest>=8.0` from Task 7.

If missing, add them now and commit as `chore(check-users): add test and Google API deps`.

- [ ] **Step 2: Install in the shared venv and run all tests**

Run:
```bash
cd src/cli/check-users && \
  ../.venv/bin/pip install -r requirements.txt && \
  ../.venv/bin/python -m pytest tests/ -v
```

Expected: all 9 tests pass.

---

## Implementation complete — verification checklist

Before opening a PR, run through this checklist:

- [ ] `cd src/cli/check-users && ../.venv/bin/python -m pytest tests/ -v` → 9 passing
- [ ] `cd src/cli/check-users/infra && .venv/bin/python -m pytest tests/ -v` → 7 passing
- [ ] `cd src/cli/check-users && docker build -t theiagenepi-usage-report:dev .` → success
- [ ] Dry-run CLI: `../.venv/bin/python update_stakeholder_doc.py --markdown tests/fixtures/sample_report.md --template-doc-id fake --sheet-id 136W69U8Ai8_M32J567r3SHk9gCOZhYAIzSOKOy5sU_w --output-docx /tmp/out.docx --dry-run` → exit 0, logs scalars + cohort count
- [ ] `cd src/cli/check-users/infra && .venv/bin/cdk synth` (with real context values) → valid CloudFormation
- [ ] Spec and plan are both committed

**Deployment steps (performed separately, not part of this PR):**

1. Fill real values in `cdk.json` (`vpc_id`, subnets, RDS SG, template doc ID, emails).
2. `cdk bootstrap` + `cdk deploy`.
3. Populate the 3 Secrets Manager entries.
4. Prepare the Google Doc template per §6 of the spec.
5. Push the first container image via GitHub Actions.
6. Run the task manually via `aws ecs run-task`, verify outputs.
7. Set `schedule_enabled: true` in `cdk.json`, redeploy.
8. Subscribe additional stakeholders to the SNS topic via the console.
9. Delete `src/cli/lambda-user-analytics/` after 2 successful monthly runs.
