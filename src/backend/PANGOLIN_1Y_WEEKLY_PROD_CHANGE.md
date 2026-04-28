# Pangolin Weekly Schedule Cutover — Manual Production Change

**Date**: 2026-04-28
**Owner**: thanh@cloudbinfies.com
**Authorized by**: thanh@cloudbinfies.com (auto-mode session)
**Type**: Manual AWS console-equivalent change. **Not in Terraform.** Will be reverted on next Terraform apply unless this doc is acted on.

---

## What changed

The scheduled prod pangolin run is now the **1-year filtered** WDL, fired weekly. The old unfiltered Thursday cron is **disabled**.

| EventBridge rule | Before | After |
|---|---|---|
| `genepi-geprod-geprodstack-pangolin-sfn` | `ENABLED`, `cron(0 23 ? * THU *)`, target = `pangolin.wdl-v0.0.1.wdl`, image `sha-6b2eb26`, 48 GB | **`DISABLED`** (rule kept so Terraform's `aws_cloudwatch_event_rule` resource still finds its name; only `state` toggled) |
| `genepi-geprod-geprodstack-pangolin-1y-sfn` | did not exist | **created**, `ENABLED`, `cron(0 23 ? * THU *)`, target = `pangolin-1y.wdl-v0.0.1.wdl`, image `sha-36d37fc`, 48 GB |

Net effect: every Thursday 23:00 UTC, only the 1y-filtered WDL fires. Samples with `collection_date < today − 365d` keep their existing lineage assignment until/unless the schedule is widened or a manual full run is triggered.

`pangolin-ondemand-sfn` (user-uploaded sample lineage calls) is **untouched**.

---

## Exact target input on the new rule

```json
{
  "Input": {
    "Run": {
      "aws_region": "us-west-2",
      "docker_image_id": "654654542669.dkr.ecr.us-west-2.amazonaws.com/genepi-pangolin:sha-36d37fc",
      "genepi_config_secret_name": "geprod/genepi-config",
      "remote_dev_prefix": "",
      "max_collection_age_days": 365
    }
  },
  "OutputPrefix": "s3://swipe-genepi-geprod-genepi/swipe/pangolin-1y-sfn/results",
  "RUN_WDL_URI": "s3://swipe-wdl-genepi-geprod-genepi/pangolin-1y.wdl-v0.0.1.wdl",
  "RunEC2Memory": 48000,
  "RunEC2Vcpu": 1,
  "RunSPOTMemory": 48000,
  "RunSPOTVcpu": 1,
  "StateMachineArn": "arn:aws:states:us-west-2:010928203514:stateMachine:genepi-geprod-swipe-default-wdl"
}
```

Memory matches the prior canonical `pangolin-sfn` value (48 GB). The 1y filter cuts dataset size dramatically, so this is overprovisioned — safe. Tune down once we have one full run's footprint to look at.

---

## Backups taken before the change

| File | Contents |
|---|---|
| `ssm-backups/eventbridge-pangolin-sfn-rule-2026-04-28-pre-disable.json` | The old rule's full describe-rule output (state=ENABLED, schedule, ARN). |
| `ssm-backups/eventbridge-pangolin-sfn-targets-2026-04-28-pre-disable.json` | The old rule's target (target_id, SFN ARN, role ARN, full input JSON). |
| `ssm-backups/geprod-pangolin-sfn-2026-04-28-pre-disable.json` | The `/genepi/geprod/geprodstack/pangolin-sfn` SSM parameter value at change time. |

---

## How to revert

```bash
PROFILE=010928203514_AdministratorAccess
REGION=us-west-2
cd <repo root>

# 1. Re-enable the old unfiltered Thursday cron.
aws events enable-rule \
  --name "genepi-geprod-geprodstack-pangolin-sfn" \
  --region $REGION --profile $PROFILE

# 2. Remove the new 1y rule + its target.
aws events remove-targets \
  --rule "genepi-geprod-geprodstack-pangolin-1y-sfn" \
  --ids "genepi-geprod-geprodstack-pangolin-1y-sfn" \
  --region $REGION --profile $PROFILE
aws events delete-rule \
  --name "genepi-geprod-geprodstack-pangolin-1y-sfn" \
  --region $REGION --profile $PROFILE
```

---

## Drift risk: this WILL be reverted by Terraform

The change is entirely in the AWS control plane. `.happy/terraform/modules/sfn_config/cron.tf` defines the existing rule with no explicit `state` argument, which defaults to `ENABLED`. On any next `terraform apply` (i.e. any push to `trunk` that triggers `push-tests.yml`):

1. Terraform sees `genepi-geprod-geprodstack-pangolin-sfn` is `DISABLED`, drift-corrects it back to `ENABLED`.
2. Terraform does **not** know about `genepi-geprod-geprodstack-pangolin-1y-sfn` at all, so it leaves it alone — both rules will run on Thursday at 23:00 UTC. That is **not** what we want; the 1y rule will overlap with the unfiltered rule.

**Mitigations (pick one before next trunk push):**

- **A. Make it permanent in Terraform.** Add a second `module "sfn_config"` invocation in `.happy/terraform/modules/ecs-stack/` (or wherever the existing pangolin module call lives) for the 1y variant, set `schedule_expressions = ["cron(0 23 ? * THU *)"]`, point `wdl_path` at a checked-in `pangolin-1y.wdl`. Then either set the existing pangolin cron to `[]` (no schedule) or add a `state = "DISABLED"` argument. Requires copying `pangolin-1y.wdl-v0.0.1.wdl` into `.happy/terraform/modules/sfn_config/`.
- **B. Hold the line manually.** Re-disable the old rule and re-create the new rule after every Terraform apply. Fragile; only acceptable if we expect to revert within a week.
- **C. Lifecycle-ignore the state field.** Add `lifecycle { ignore_changes = [is_enabled, state] }` to the rule resource in Terraform so manual disables stick. Cleaner than (B), still doesn't manage the new rule, so (A) is the right long-term answer.

---

## Verification before next Thursday

1. Confirm the new rule fires by inspecting CloudWatch metric `AWS/Events:Invocations` for rule `genepi-geprod-geprodstack-pangolin-1y-sfn` after Thursday 23:00 UTC.
2. Confirm the old rule does **not** fire — same metric, rule `genepi-geprod-geprodstack-pangolin-sfn`, should have zero invocations.
3. Confirm the SFN `genepi-geprod-swipe-default-wdl` shows a fresh execution named `genepi-geprod-geprodstack-pangolin-1y-sfn-...` (auto-named by EventBridge).
4. Confirm the run's `OutputPrefix` lands in `s3://swipe-genepi-geprod-genepi/swipe/pangolin-1y-sfn/results/pangolin-1y.wdl-N/`.
5. Confirm DB writes via:
   ```sql
   SELECT DATE(sl.last_updated), sl.lineage_software_version, COUNT(*)
   FROM samples s
   JOIN sample_lineages sl ON sl.sample_id = s.id
   WHERE s.pathogen_id = (SELECT id FROM pathogens WHERE slug = 'SC2')
     AND sl.last_updated >= CURRENT_DATE - INTERVAL '2 days'
   GROUP BY 1, 2 ORDER BY 1 DESC, 3 DESC;
   ```
   Should show a chunk of rows with `last_updated = 2026-05-XX` (next Thursday) on the current pangolin version.

---

## What is intentionally NOT touched

- `pangolin-ondemand-sfn` (SSM + EventBridge): **unchanged**. User-uploaded samples still get the full unfiltered lineage call on the prior image.
- `/genepi/geprod/geprodstack/pangolin-sfn` (SSM parameter): **unchanged**. Direct SSM edits would be reverted by Terraform too, and the EventBridge schedule does not actually read SSM at fire time — the input is embedded in the rule's target. The SSM is only there as a Terraform-managed mirror of the same JSON.
- Backend ECS service: **unchanged**. The backend's SSM cache only matters for `pangolin-ondemand-sfn`.
- Terraform code: **unchanged** by user request. See the drift section above for what this means.
- `trunk`: this manual change has no commit. Document this MD before merging anything else to `trunk`.

---

## Background — why this change

Today (2026-04-28), the 1y filter variant was validated end-to-end against group 69 (Chicago DPH, 17,892 SC2 samples) and the filtered WDL reached production-grade reliability:

- 1y filter dry-run estimate matched reality.
- The filtered WDL persisted lineage rows correctly (`save.py` wrote 16,442 rows to `sample_lineages` in a single 8h 37m run).
- The image (`sha-36d37fc`) is a strict superset of the prior canonical image — `find_samples.py` adds an optional `--max-collection-age-days` flag that defaults to off.

See `PANGOLIN_1Y_FILTER_EXPERIMENT.md` for the original experiment writeup.
