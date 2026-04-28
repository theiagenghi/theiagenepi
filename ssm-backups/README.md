# ssm-backups/

**Do not commit JSON files in this directory.** `.gitignore` excludes
`ssm-backups/*.json` so accidental commits do not leak prod state to this
public repo.

## What this directory is

A local working area for snapshots taken before manual production changes —
SSM parameters, EventBridge rules, etc. Used as a safety net so a manual
change can be rolled back to the exact prior state.

## What it is NOT

A long-term backup store. Anything here is ephemeral and should be uploaded
to private storage as soon as the change window is over.

## Standard snapshot pattern

```bash
PROFILE=010928203514_AdministratorAccess
REGION=us-west-2

# SSM parameter
aws ssm get-parameter \
  --name "/genepi/geprod/geprodstack/<param>" \
  --with-decryption \
  --region $REGION --profile $PROFILE \
  > ssm-backups/<param>-$(date +%Y-%m-%d).json

# EventBridge rule + targets
aws events describe-rule --name "<rule>" --region $REGION --profile $PROFILE \
  > ssm-backups/eventbridge-<rule>-$(date +%Y-%m-%d).json
aws events list-targets-by-rule --rule "<rule>" --region $REGION --profile $PROFILE \
  > ssm-backups/eventbridge-<rule>-targets-$(date +%Y-%m-%d).json
```

## Long-term destination — TODO

The current local-only flow is a stop-gap. The right home is a private,
versioned S3 bucket (e.g., `s3://genepi-ops-state/ssm-backups/`) or a
dedicated private GitHub repo for ops state. Until that is provisioned,
treat any file in this directory as scratch.

Once the destination exists, the snapshot pattern becomes a one-liner:

```bash
aws ssm get-parameter ... | aws s3 cp - s3://genepi-ops-state/ssm-backups/<file>
```

## Pre-existing committed files

A handful of pre-policy backup files already live in this directory and in
`git log`. They were audited on 2026-04-28 and confirmed to contain no
credentials (no AWS keys, no DB passwords, no Auth0 / Split.io tokens) —
only ARNs, account IDs, S3 bucket names, and Secrets-Manager secret *names*
(not their values). Those files are kept where they are; do not add to them.

## Why this matters

`aws ssm get-parameter --with-decryption` will dump the **plaintext** of any
`SecureString` parameter into the output JSON. There is nothing in the AWS
CLI that flags this as risky. If such a file lands in a public repo it is
permanently public; rotating the underlying secret is the only mitigation.
The `.gitignore` rule plus a `gitleaks` scan are the two automated guards
preventing that.
