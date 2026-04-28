# Pangolin Group + Date Filter Variant (Experimental)

**Status**: experimental, manually invoked only. Not on the prod schedule.
**Started**: 2026-04-28
**Owner**: thanh@cloudbinfies.com
**Follow-up to**: `PANGOLIN_1Y_FILTER_EXPERIMENT.md`

---

## Why this exists

The 1-year variant from 2026-04-27 (`pangolin-1y.wdl-v0.0.1.wdl`) showed the
date filter behaves correctly and reduced the per-run dataset by ~97%. Next
question: can we also scope a re-lineaging run to **a specific submitting
group** so a single jurisdiction can be re-processed without touching the rest
of the DB?

Use cases:

- A health department reports a lineage drift in their samples and wants a
  fresh re-call without waiting for the next Thursday cron.
- We want to test a new pangolin version against one group's samples first.
- Re-processing only "young" samples for a single group when a regional
  outbreak prompts a re-evaluation.

---

## What changed

### `find_samples.py`

Adds an optional `--submitting-group-id` Click flag (`multiple=True`). When
omitted, the SQL is byte-identical to today's behavior. When set, adds a
single `WHERE submitting_group_id IN (...)` clause. Composes freely with
`--max-collection-age-days`.

### `pangolin-filtered.wdl-v0.0.1.wdl`

New WDL variant that exposes both filters. Defaults: no age filter, no group
filter. Both inputs are optional; passing neither yields unfiltered behavior
identical to `pangolin.wdl-v0.0.1.wdl`.

### Test

`test_pangolin_find_samples_submitting_group_ids` — covers single-group,
multi-group, and unfiltered cases.

---

## Backward-compatibility properties

The new image (whatever sha is built from this branch) is a **strict superset**
of the prior pangolin image:

- New `submitting_group_ids` parameter defaults to `None`/`[]` — no SQL change.
- The existing `pangolin.wdl-v0.0.1.wdl` and `pangolin-1y.wdl-v0.0.1.wdl` do
  not pass the new flag, so they get exactly today's behavior.
- All other binaries / configs in the image are unchanged.

Implication: same as the 1y variant — even if accidentally written to the
prod SSM parameter, the scheduled run would behave identically because the
canonical WDL never asks for the filter.

---

## Dry-run estimate (RIPHL-unsub, group_id 69)

Querying prod on 2026-04-28:

| Filter combination | Samples selected |
|---|---:|
| `submitting_group_ids=[69]` only | 17,893 |
| `submitting_group_ids=[69]` + `max_collection_age_days=365` | 1,454 |
| `submitting_group_ids=[69]` + `max_collection_age_days=180` | 844 |
| Above filters AND lineage version != latest v1.38 (post-yesterday) | 4 |

The "4" is what `find_samples.py` would actually emit after `should_sample_be_updated`
prunes already-current samples — most 1-year RIPHL samples already got v1.38
from yesterday's run. So a re-run of the same scope today would be a no-op
(or near-no-op).

---

## How to start a manual run (template)

After building an image from this branch (any sha that includes both the
date and group flag), invoke the SFN with:

```bash
EXEC_NAME="pangolin-filtered-group69-1y-$(date +%Y-%m-%d-%H-%M)"
INPUT='{
  "Input": {
    "Run": {
      "aws_region": "us-west-2",
      "docker_image_id": "654654542669.dkr.ecr.us-west-2.amazonaws.com/genepi-pangolin:<NEW_SHA>",
      "genepi_config_secret_name": "geprod/genepi-config",
      "remote_dev_prefix": "",
      "max_collection_age_days": 365,
      "submitting_group_ids": [69]
    }
  },
  "OutputPrefix": "s3://swipe-genepi-geprod-genepi/swipe/pangolin-filtered-sfn/results",
  "RUN_WDL_URI": "s3://swipe-wdl-genepi-geprod-genepi/pangolin-filtered.wdl-v0.0.1.wdl",
  "RunEC2Memory": 32000,
  "RunEC2Vcpu": 1,
  "RunSPOTMemory": 32000,
  "RunSPOTVcpu": 1,
  "StateMachineArn": "arn:aws:states:us-west-2:010928203514:stateMachine:genepi-geprod-swipe-default-wdl"
}'

aws stepfunctions start-execution \
  --state-machine-arn "arn:aws:states:us-west-2:010928203514:stateMachine:genepi-geprod-swipe-default-wdl" \
  --name "$EXEC_NAME" \
  --input "$INPUT" \
  --region us-west-2 \
  --profile 010928203514_AdministratorAccess
```

**Memory**: dropped to 32 GB (vs 118 GB for the 1y variant) — 1,454 samples
is a small fasta. Adjust if running with a wider date window or no date
filter on a large group.

---

## Pre-launch checklist

- [ ] Build a new `genepi-pangolin` image from this branch (CI workflow:
      `build-and-push-ecr.yml` on the matching branch).
- [ ] Upload `pangolin-filtered.wdl-v0.0.1.wdl` to
      `s3://swipe-wdl-genepi-geprod-genepi/`.
- [ ] Confirm test passes:
      `pytest src/backend/aspen/workflows/pangolin/tests/test_pangolin_workflow.py::test_pangolin_find_samples_submitting_group_ids`
- [ ] Confirm test passes for the existing 1y filter (regression):
      `::test_pangolin_find_samples_max_collection_age`

---

## What is intentionally NOT touched

- `pangolin-1y.wdl-v0.0.1.wdl` — left frozen as the verified 1y artifact.
- `pangolin.wdl-v0.0.1.wdl` — unchanged.
- `.happy/terraform/**` — no Terraform changes.
- SSM parameters — both `pangolin-sfn` and `pangolin-ondemand-sfn` still pin
  the prior image.
- EventBridge rules — Thursday cron unchanged.
- `trunk` — this branch is not merged.
