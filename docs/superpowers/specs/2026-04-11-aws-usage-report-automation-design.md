# AWS Automation: TheiaGenEpi Monthly Usage Report

**Status:** Design (not yet implemented)
**Author:** Thanh Le Viet
**Date:** 2026-04-11

## 1. Purpose

Automate the monthly TheiaGenEpi usage report workflow that today runs manually
from a laptop via Tailscale VPN. The job currently:

1. Queries the private RDS `aspen_db` for users, samples, and phylogenetic runs
2. Queries Auth0 for user login metadata
3. Reads a Google Sheets "account request" spreadsheet
4. Writes `user_activity_report.md` (raw detailed report)
5. Updates a fixed Google Spreadsheet (ID `136W69U8Ai8_M32J567r3SHk9gCOZhYAIzSOKOy5sU_w`)
6. Is manually followed by a hand-edit of `USAGE_SUMMARY_REPORT.md` and a
   stakeholder-facing Word document (`TheiaGenEpi usage analysis-2_2026.docx`)

After this change the entire pipeline runs on AWS on a schedule, updates a
stakeholder Google Doc automatically, archives both markdown and `.docx`
outputs in S3, and emails the team a link.

## 2. Non-Goals

- No rewrite of `report-theiagenepi-usage.py` (only a small change to load the
  Google service account from an env var instead of a file).
- No CI/CD pipeline for the infrastructure itself beyond what GitHub Actions
  provides for image build/push. `cdk deploy` is run from a maintainer laptop.
- No staging/prod branching gates. Manual trigger → verify → enable schedule.
- No refactor of the existing Happy/Terraform stack. This project is
  deliberately independent.
- No migration of the `src/cli/lambda-user-analytics/` prior-art stack in this
  change. It is flagged for deletion as a follow-up once this stack is proven.

## 3. Architecture Overview

```
EventBridge Scheduler  ──►  ECS Fargate Task  ──►  report-theiagenepi-usage.py
   (cron: 0 9 1 * *)         (private subnet)             │
                                                          ├──► RDS (private, VPC)
                                                          ├──► Auth0 API (via NAT)
                                                          ├──► Google Sheets API (via NAT)
                                                          ├──► Google Docs API (via NAT)
                                                          ├──► Google Drive API (via NAT)
                                                          ├──► S3 bucket (report archive)
                                                          └──► SNS topic (email)
```

**Compute.** A dedicated ECS cluster `theiagenepi-usage-report` runs a single
Fargate task on a schedule. Task size 0.5 vCPU / 1 GB, arm64. Runs in the
existing Genepi VPC's private subnets (looked up by ID), egresses through the
VPC's existing NAT Gateway for Auth0, Google, and ECR pulls.

**Schedule.** EventBridge Scheduler, cron `0 9 1 * *` UTC (09:00 UTC on day 1
of each month). Retry policy 3× with exponential backoff, 15-minute flexible
window, failure destination = SNS topic.

**Image.** `src/cli/check-users/Dockerfile` (new) produces a container with
Python 3.11, the script's dependencies (`psycopg2-binary`, `requests`,
`gspread`, `google-auth`, `google-api-python-client`, `python-dotenv`), the
existing `report-theiagenepi-usage.py`, and a new `update_stakeholder_doc.py`.
Pushed to ECR repo `theiagenepi/usage-report` by a GitHub Actions workflow
using OIDC role assumption (no static AWS keys).

**Secrets.** Three Secrets Manager entries:

| Secret                             | Contents                                                      |
|------------------------------------|---------------------------------------------------------------|
| `theiagenepi/prod/db`              | JSON: `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`          |
| `theiagenepi/prod/auth0`           | JSON: `AUTH0_DOMAIN`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`|
| `theiagenepi/prod/google-sa`       | Full Google service account JSON as a single string          |

Injected into the container as env vars via the ECS task definition's
`secrets` block. The script gets a ~5-line change to load the Google SA JSON
via `Credentials.from_service_account_info(json.loads(os.environ["GOOGLE_SA_JSON"]))`
instead of reading a file path, so the filesystem stays untouched.

**Outputs.** Per run, the task writes:

- `s3://theiagenepi-usage-reports/YYYY/MM/user_activity_report.md`
- `s3://theiagenepi-usage-reports/YYYY/MM/theiagenepi-usage-analysis.docx`

Bucket is versioned, SSE-S3 encrypted, all public access blocked, with a
lifecycle rule transitioning objects to Glacier Instant Retrieval after 90
days and expiring non-current versions after 7 years.

**Notifications.** SNS topic `theiagenepi-usage-report-notifications` with
email subscriptions. Published message contains:

- Report month
- Pre-signed S3 URLs (7-day validity) for the `.md` and `.docx`
- Live Google Doc URL (stakeholder-facing)
- Live Google Sheet URL (existing)
- Run duration and exit status

Any task failure also publishes a failure message to the same topic via
EventBridge Scheduler's dead-letter target.

**What's reused vs created.**

| Reused                                  | Created                                                |
|-----------------------------------------|--------------------------------------------------------|
| Existing Genepi VPC                     | ECR repository                                         |
| Existing private subnets                | ECS cluster, task definition, task + execution roles   |
| Existing NAT Gateway                    | EventBridge Scheduler + schedule role                  |
| Existing RDS instance + security group  | 3 Secrets Manager entries                              |
| Existing Google service account         | S3 bucket + lifecycle policy                           |
| Existing Google Sheet (fixed ID)        | SNS topic + subscriptions                              |
| Existing `TheiaGenEpi usage analysis.docx` (uploaded as Google Doc template) | CloudWatch log group, alarm |

## 4. CDK Stack Layout

One CDK Python app at `src/cli/check-users/infra/`:

```
src/cli/check-users/infra/
├── app.py                  # CDK app entry
├── cdk.json                # Context: VPC ID, subnets, RDS SG, template doc ID, emails
├── requirements.txt        # aws-cdk-lib, constructs
├── README.md               # How to discover required IDs, deploy, rotate secrets
└── theiagenepi_usage_report/
    ├── __init__.py
    └── stack.py            # TheiagenepiUsageReportStack
```

The stack is split into logical constructs inside a single file. Total
expected size ~250 lines of Python.

| Construct              | Responsibility                                                                 |
|------------------------|--------------------------------------------------------------------------------|
| `NetworkingLookup`     | `Vpc.from_lookup`, subnet IDs, RDS `SecurityGroup.from_lookup_by_id`           |
| `ReportImage`          | `ecr.Repository` with lifecycle (keep last 10 images)                          |
| `ReportSecrets`        | 3 `secretsmanager.Secret` entries; CDK creates empty, populated manually once  |
| `ReportStorage`        | `s3.Bucket` versioned + SSE-S3 + lifecycle (Glacier IR at 90d, expire at 7yr)  |
| `ReportNotifications`  | `sns.Topic` + email subscriptions from context                                 |
| `ReportTask`           | `ecs.Cluster`, `FargateTaskDefinition`, task/execution roles, security group   |
| `ReportSchedule`       | `scheduler.CfnSchedule` + schedule execution role                              |
| `ReportObservability`  | CloudWatch log group (30-day retention) + `TaskFailureCount` alarm             |

All stack parameters (VPC ID, subnet IDs, RDS SG ID, notification emails,
Google Doc template ID) come from `cdk.json` context. No hard-coded IDs.

**IAM surface (minimum permissions):**

- **Task role:**
  - `secretsmanager:GetSecretValue` on the 3 secrets only
  - `s3:PutObject`, `s3:PutObjectAcl` on `theiagenepi-usage-reports/*`
  - `s3:GetObject` for generating pre-signed URLs
  - `sns:Publish` on the notifications topic
- **Execution role:**
  - Standard `AmazonECSTaskExecutionRolePolicy` (ECR pull, CloudWatch Logs)
  - `secretsmanager:GetSecretValue` on the 3 secrets (for env injection)
- **Schedule execution role:**
  - `ecs:RunTask` on the task definition
  - `iam:PassRole` on the task + execution roles

## 5. Data Flow Per Run

```
1. EventBridge Scheduler fires at 09:00 UTC on day 1 of the month.
2. Scheduler calls ecs:RunTask; Fargate provisions a task in the private subnet.
3. ECS pulls the image from ECR; injects secrets as env vars.
4. Container entrypoint runs the pipeline:

   Step A - Generate raw report:
     python report-theiagenepi-usage.py \
       --start-date 2024-11-15 \
       --output /tmp/user_activity_report.md \
       --spreadsheet
     ├─ Connects to RDS via private subnet
     ├─ Fetches Auth0 users via NAT
     ├─ Updates Google Sheet (existing behavior)
     └─ Writes /tmp/user_activity_report.md

   Step A' - Preserve raw markdown immediately:
     aws s3 cp /tmp/user_activity_report.md s3://.../YYYY/MM/
     (run unconditionally after Step A so the raw data survives any
      downstream failure)

   Step B - Update stakeholder Google Doc (new):
     python update_stakeholder_doc.py \
       --markdown /tmp/user_activity_report.md \
       --template-doc-id ${TEMPLATE_DOC_ID} \
       --sheet-id 136W69U8Ai8_M32J567r3SHk9gCOZhYAIzSOKOy5sU_w \
       --output-docx /tmp/theiagenepi-usage-analysis.docx
     ├─ Parses the markdown for scalar stats (total_users, retention %, etc.)
     ├─ Reads the Monthly Activity table from the Google Sheet
     ├─ Drive API: copy template doc, rename to "TheiaGenEpi usage analysis - YYYY-MM"
     ├─ Docs API batchUpdate:
     │    - ReplaceAllText for scalar placeholders
     │    - Regenerate the cohort list between {{cohorts_start}}/{{cohorts_end}}
     │    - Delete existing data rows in the monthly table, insert fresh rows
     ├─ Drive API: files.export to .docx → /tmp/theiagenepi-usage-analysis.docx
     └─ Emits live Google Doc URL to stdout

   Step C - Archive docx + notify:
     aws s3 cp /tmp/theiagenepi-usage-analysis.docx s3://.../YYYY/MM/
     aws sns publish --topic-arn ${SNS_TOPIC_ARN} --message "$(build_message.py)"

5. Container exits 0. On any non-zero exit, EventBridge Scheduler retries up to
   3x, then delivers a failure message to the same SNS topic via its DLQ
   target.
```

## 6. Google Doc Template Preparation (one-time manual step)

The existing `src/cli/check-users/TheiaGenEpi usage analysis-2_2026.docx` is
the source of truth for layout. To prepare the template:

1. Upload the `.docx` to the team's Google Drive, open as a Google Doc, save
   a copy named `TheiaGenEpi usage analysis - TEMPLATE`.
2. Share the template doc with the existing service account email (the same
   one used by Google Sheets today, found in
   `spreadsheet-303717-623d4a500e34.json` → `client_email`).
3. Record the template Doc ID from the URL and add it to `cdk.json` context as
   `template_doc_id`.
4. Edit the template: replace dynamic content with placeholders. The required
   placeholder set is:

   **Scalar replacements (using `ReplaceAllText`):**

   | Placeholder                   | Source (from markdown/sheet)                          |
   |-------------------------------|-------------------------------------------------------|
   | `{{reporting_date}}`          | Month + year of the run (e.g., "April 2026")          |
   | `{{total_users}}`             | "Total: N users registered since November 15, 2024"   |
   | `{{multi_login_count}}`       | Users with >5 logins                                  |
   | `{{multi_login_pct}}`         | Percentage with >5 logins                             |
   | `{{regular_user_count}}`      | Users with 1-5 logins                                 |
   | `{{regular_user_pct}}`        | Percentage with 1-5 logins                            |
   | `{{active_last_30d_count}}`   | Users logged in within 30 days                        |
   | `{{active_last_30d_pct}}`     | Percentage logged in within 30 days                   |
   | `{{avg_days_since_login}}`    | Average days since last login                         |
   | `{{date_range_end}}`          | Report period end date (e.g., "12/04/2026")           |
   | `{{samples_total}}`           | Total samples deposited                               |
   | `{{samples_top_org}}`         | Top contributing org name                             |
   | `{{samples_top_org_count}}`   | Sample count for top org                              |
   | `{{phylo_total}}`             | Total phylo runs                                      |
   | `{{phylo_groups}}`            | Count of groups running phylo                         |
   | `{{phylo_top_group}}`         | Top group running phylo                               |
   | `{{preexisting_logged_in}}`   | Preexisting users who logged in post-transition       |
   | `{{support_tickets_month}}`   | Tickets in reporting month                            |

   **Region replacements (using delimiter markers):**

   - `{{cohorts_start}} ... {{cohorts_end}}` — wraps the "Activity by
     Registration Cohort" bulleted list. On each run the script deletes
     everything between the markers and inserts a fresh list, preserving the
     markers.

   **Table replacement:**

   - The Monthly Activity Overview table (`Month | New Users | Active Users |
     Total Samples | Total Phylo Runs`) is identified as the first table in
     the document with header cell `Month`. The script deletes all data rows
     and inserts fresh rows built from the Google Sheet contents.

5. Verify by running the job manually once against the template (see §8).

**Template drift protection.** The `update_stakeholder_doc.py` script
validates that all required placeholders exist in the template before making
any edits. Missing placeholder → fail fast, no partial update.

## 7. Error Handling & Observability

- **Retries.** EventBridge Scheduler 3× with exponential backoff handles
  transient DB / Auth0 / Google API failures without human action.
- **Alarms.** Single CloudWatch alarm on ECS `TaskFailureCount > 0` over one
  run, publishing to the SNS topic. No metric filter chain; one alarm is
  enough for a monthly job.
- **Logs.** Fargate writes to CloudWatch Logs `/ecs/theiagenepi-usage-report`,
  30-day retention. The script keeps its existing stdout logging — no logging
  refactor in scope.
- **Secret failures.** If Secrets Manager values are missing or malformed,
  the container fails at startup and ECS reports the failure; SNS alerts.
  There is no silent fallback to defaults.
- **Google Doc drift.** If the template has been edited such that required
  placeholders are missing, `update_stakeholder_doc.py` fails loudly before
  any Docs API write, leaving the template untouched.
- **Partial failure.** Step A' always uploads the raw markdown to S3
  immediately after Step A, before Step B runs. If Step B (Google Doc) or
  Step C fails, the raw markdown is already preserved in S3 and the SNS
  failure message includes its S3 URL.

## 8. Testing & Rollout

**Local dev loop:**

- `docker build` + `docker run` with `.env.test` pointing at a non-prod DB.
- The existing `report-theiagenepi-usage.py` already runs standalone — the
  container only wraps it.
- `update_stakeholder_doc.py` has a `--dry-run` flag that logs the
  `batchUpdate` payload it would send without calling the Docs API.

**First deploy:**

1. `cdk deploy` creates infra. Secrets are created empty.
2. Populate the 3 secrets manually via AWS CLI or console (one-time).
3. EventBridge schedule is created **disabled** (`State: DISABLED`).
4. Build and push the first image via the GitHub Actions workflow.
5. Manual trigger: `aws ecs run-task --cluster theiagenepi-usage-report ...`
6. Verify:
   - CloudWatch logs show successful Step A / B / C
   - `user_activity_report.md` and `.docx` appear in S3
   - The live Google Doc copy looks correct (narrative, cohort list, table)
   - SNS email arrives with working links
7. Enable the schedule: `aws scheduler update-schedule ... --state ENABLED`.

**Staging environment.** Optional. A second stack instance via
`cdk deploy --context env=staging` with a separate DB / Google Doc template /
S3 bucket. Recommended once but not required given the once-a-month cadence.

**Rollout sequencing:**

- Month 1: keep running the manual workflow in parallel with the scheduled
  job; compare outputs.
- Month 2: if outputs match, rely on the automated job. Delete
  `src/cli/lambda-user-analytics/` in a separate PR.
- Month 3+: steady state; only human intervention is reviewing the SNS email.

## 9. Secrets Rotation

- `theiagenepi/prod/db` and `theiagenepi/prod/auth0` have no automatic
  rotation configured. Rotation is manual via CLI when credentials change.
- `theiagenepi/prod/google-sa` same — manually updated if the service account
  key is rotated.
- No `RotationSchedule` resource is created. Automatic rotation is out of
  scope; the job runs once a month and secret access is audited via
  CloudTrail.

## 10. Cost Estimate

Rough monthly cost in us-west-2, assuming one 5-minute run per month:

| Resource                          | Cost                        |
|-----------------------------------|-----------------------------|
| Fargate compute (0.5 vCPU / 1 GB, 5 min) | < $0.01               |
| ECR storage (1 GB image, 10 versions)    | ~$1.00                |
| Secrets Manager (3 secrets)              | $1.20                 |
| S3 storage (few MB/month, 7yr lifecycle) | < $0.10               |
| CloudWatch Logs (30-day)                 | < $0.05               |
| EventBridge Scheduler + SNS              | negligible            |
| NAT Gateway data transfer                | negligible (existing) |
| **Total**                                | **~$2.50 / month**    |

The NAT Gateway itself is not counted — it already exists for the Aspen
workloads. If this were a net-new VPC, the NAT Gateway alone would add ~$32/mo.

## 11. Open Questions / Follow-ups

1. **Schedule holidays.** Should the job skip months when the 1st falls on a
   weekend? No — the report doesn't care about business days.
2. **Multiple environments.** Do we want a staging stack, or is test-in-prod
   acceptable? Recommendation: skip staging unless an issue surfaces.
3. **Report retention in S3.** 7-year expiry for non-current versions is a
   guess; confirm with whoever owns compliance.
4. **Notification recipients.** Which emails subscribe to the SNS topic? List
   needs to be collected before `cdk deploy`.
5. **Secret rotation policy.** If the team later wants automatic rotation,
   add `RotationSchedule` in a follow-up.
6. **Deletion of `lambda-user-analytics/`.** Scheduled for a follow-up PR
   once this stack has run successfully twice.

## 12. Implementation Checklist (for the plan stage)

Listed here for the follow-up `writing-plans` step; not executed in this doc.

- [ ] Write `src/cli/check-users/Dockerfile`
- [ ] Add `update_stakeholder_doc.py` + unit tests
- [ ] Modify `report-theiagenepi-usage.py` to load Google SA from env var
- [ ] Scaffold `src/cli/check-users/infra/` CDK Python project
- [ ] Implement `TheiagenepiUsageReportStack`
- [ ] Add GitHub Actions workflow for OIDC image build + push
- [ ] Prepare Google Doc template with placeholders
- [ ] Populate 3 Secrets Manager entries
- [ ] Run first manual invocation, verify outputs
- [ ] Enable EventBridge schedule
- [ ] Subscribe stakeholders to SNS topic
