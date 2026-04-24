# Change Log — 2026-04-23: Pangolin PUSHER Version Fix

**Author**: Thanh Lee  
**Issue**: [#28 — UShER/pUShER version remains at v1.34](https://github.com/theiagenghi/theiagenepi/issues/28)  
**Branch**: `tghi-docker-pangolin-bookworm`  
**CI run**: [24775324871](https://github.com/theiagenghi/theiagenepi/actions/runs/24775324871) — all 6 images passed  
**New pangolin image**: `654654542669.dkr.ecr.us-west-2.amazonaws.com/genepi-pangolin:sha-5ab2c64`

---

## Problem

Users reported that samples uploaded since early 2026 showed `PUSHER-v1.34` in lineage
output, despite `pangolin-data v1.37` (Jan 2026) and `v1.38` being available.

---

## Root Cause

Three compounding issues:

**1. Broken pangolin CI/CD build (primary)**  
`Dockerfile.pangolin` used `debian:bullseye` (glibc 2.31) as the base. As of ~March 2025,
compiled UShER binaries began requiring `GLIBC_2.33+` at runtime, crashing the container.
This silently broke every pangolin image built via the normal `build-and-push-ecr.yml`
pipeline — the image built but the container failed at runtime.

**2. Manual workaround frozen at v1.34**  
A separate repo (`docker-pangolin-fix`) was used to layer `pangolin-data` updates on top
of a known-working image (`sha-713e6f8`). This workaround was last updated to `v1.34` and
never bumped further, so `v1.35`–`v1.38` never reached production.

**3. On-demand runs bypass `update_pangolin.sh`**  
The scheduled workflow (`pangolin.wdl`) calls `update_pangolin.sh` before running, which
would pull the latest pangolin-data. However, the on-demand workflow (`pangolin-ondemand.wdl`)
skips this step and runs against whatever is baked into the Docker image directly.

---

## Changes Made

### 1. `src/backend/Dockerfile.pangolin`

| Field | Before | After |
|---|---|---|
| Build base | `debian:bullseye` (glibc 2.31) | `debian:bookworm` (glibc 2.36) |
| Runtime base | `python:3.10-slim-bullseye` | `python:3.10-slim-bookworm` |
| pangolin | `v4.3.1` | `v4.3.4` |
| pangolin-data | `v1.31` | `v1.38` |
| libprotobuf | `libprotobuf23` | `libprotobuf32` |
| libtbb | `libtbb2` | `libtbb12` |
| libtbb symlink | `libtbb.so.2 → libtbb_preview.so.2` | `libtbb.so.12 → libtbb_preview.so.12` |

`scorpio@v0.3.19` and `constellations@v0.1.12` unchanged (already latest).

**Why bookworm**: UShER v0.6.4 built binaries now require `GLIBC_2.33` and `GLIBC_2.34`.
Bookworm ships glibc 2.36 which satisfies this. Staying on `pangolin v4.3.4` (not v4.4)
because pangolin v4.4 tightens the UShER constraint to `≤0.6.3`, which would conflict.

### 2. `.github/workflows/build-and-push-ecr.yml`

- `runs-on: ubuntu-20.04` → `ubuntu-22.04` (20.04 runners were deprecated and unavailable,
  causing all jobs to queue indefinitely)
- Added `fail-fast: false` to the matrix strategy (default `true` was cancelling all 6
  image builds when any single one failed)

### 3. `src/frontend/Dockerfile`

- Added `printf` block to redirect apt to `archive.debian.org` (Debian Buster hit EOL
  June 2024; packages moved from `deb.debian.org` to the archive)
- Added `-o Acquire::Check-Valid-Until=false` to `apt-get update` (archive has expired
  `Valid-Until` headers)
- Removed `gconf-service`, `libgconf-2-4`, `libappindicator1` (removed from Buster archive)

### 4. `src/backend/Dockerfile.backend`

- `POETRY_VERSION`: `1.3.2` → `1.5.1` in both build and base stages
- Replaced `RUN cd aspen && poetry install --no-cache --only main` with
  `RUN pip install --no-cache-dir --no-deps ./aspen`

**Why**: Poetry's startup imports `requests → charset_normalizer`. The build stage's
`COPY site-packages` carried stale sdist artifacts from when pip upgraded
`charset_normalizer` for Poetry's own deps. This caused an `ImportError` on
`_FREQUENCIES_SET` (removed in charset_normalizer 3.x) every time Poetry tried to start
in the base stage. Using `pip` avoids this entirely — pip uses its own vendored libraries
and never touches the system `charset_normalizer`. The aspen package has
`install_requires=[]`, so `--no-deps` is safe.

---

## Production Deployment

### SSM Parameter Updates (prod)

Backups saved to `ssm-backups/` before any changes.

| Parameter | Before | After |
|---|---|---|
| `/genepi/geprod/geprodstack/pangolin-sfn` | `sha-713e6f8` | `sha-5ab2c64` |
| `/genepi/geprod/geprodstack/pangolin-ondemand-sfn` | `sha-713e6f8` | `sha-5ab2c64` |

Only `docker_image_id` changed in each parameter. All other fields (OutputPrefix,
RUN_WDL_URI, memory, vcpu, StateMachineArn) are unchanged — verified via `diff` against
backup files.

### Backend ECS Restart

The backend reads `pangolin-ondemand-sfn` from SSM once at startup and caches it for the
lifetime of the process (`APISettings()` in `aspen/api/main.py` → `app.state.aspen_settings`).
A force-new-deployment was required for the new SSM value to take effect.

```
Service:  happy-geprod / geprodstack-backend
Action:   force-new-deployment
Task def: genepi-geprod-geprodstack-backend:10
Result:   COMPLETED — 1 task running, 0 failed
```

Rolling replacement with no downtime (new task healthy before old task drained).

---

## Rollback

### SSM rollback (if pangolin produces bad output)

```bash
# pangolin-sfn
VALUE=$(jq -r '.Parameter.Value' ssm-backups/geprod-pangolin-sfn-2026-04-23.json)
aws ssm put-parameter --name "/genepi/geprod/geprodstack/pangolin-sfn" \
  --value "$VALUE" --type String --overwrite --region us-west-2 \
  --profile 010928203514_AdministratorAccess

# pangolin-ondemand-sfn
VALUE=$(jq -r '.Parameter.Value' ssm-backups/geprod-pangolin-ondemand-sfn-2026-04-23.json)
aws ssm put-parameter --name "/genepi/geprod/geprodstack/pangolin-ondemand-sfn" \
  --value "$VALUE" --type String --overwrite --region us-west-2 \
  --profile 010928203514_AdministratorAccess

# Restart backend to pick up rollback
aws ecs update-service --cluster happy-geprod --service geprodstack-backend \
  --force-new-deployment --region us-west-2 --profile 010928203514_AdministratorAccess
```

### Code rollback (if new images are broken)

The Dockerfile changes are on branch `tghi-docker-pangolin-bookworm` and have not been
merged to `trunk`. Reverting is a matter of not merging the branch. The production images
were deployed directly via SSM, not via the Happy/Terraform pipeline — so `trunk` and
`locals.tf.json` are still at `sha-a3d90e3` (unchanged). **The next Terraform apply will
revert both SSM parameters back to whatever `locals.tf.json` says** — see below.

---

## Outstanding Risk

**The SSM update bypassed Terraform.** The Happy stack (`locals.tf.json`) still references
`sha-a3d90e3` for all services. The next time a Terraform apply runs (e.g. a staging or
prod deploy triggered by a trunk push), Terraform will overwrite both pangolin SSM
parameters back to `sha-a3d90e3` — which points to the old broken pangolin image.

**To prevent this**, either:

1. Merge `tghi-docker-pangolin-bookworm` to `trunk` so the next CI deploy builds and
   deploys the fixed images through the normal pipeline, OR
2. Add `pangolin = "sha-5ab2c64"` to the `image_tags` map in
   `.happy/terraform/envs/prod/main.tf` before the next Terraform apply

This is the most important follow-up action from this change.

---

## Verification

- GitHub issue [#28](https://github.com/theiagenghi/theiagenepi/issues/28) commented and
  remains open for user confirmation
- New uploads will show `PUSHER-v1.38`
- Existing samples will be updated by the next scheduled pangolin run (weekday nights,
  23:00 UTC)

---

# Change Log — 2026-04-24: UShER v0.6.3 Hotfix

**Author**: Thanh Lee  
**Related issue**: [#28](https://github.com/theiagenghi/theiagenepi/issues/28) (follow-up)  
**Branch**: `tghi-docker-pangolin-bookworm`  
**CI run**: [24888496544](https://github.com/theiagenghi/theiagenepi/actions/runs/24888496544) — all 6 images passed  
**New pangolin image**: `654654542669.dkr.ecr.us-west-2.amazonaws.com/genepi-pangolin:sha-90d8e8f`

## Problem

Every pangolin on-demand job submitted since the 2026-04-23 deploy failed within ~1 second
in the `usher_inference` Snakemake rule (exit code 1). Lineage calling was broken for all
new uploads.

Example failed execution: `KE-JROTIENO-ondemand-pangolin-2026-04-24-07-44-16-771185`  
Batch job: `c3770420-877f-450a-8d3d-ca98b418f9bc`

## Root Cause

**UShER v0.6.4 is explicitly excluded by the pangolin project** (cov-lineages/pangolin#560).
The pangolin `environment.yml` specifies `!=0.6.4,!=0.6.5` since pangolin v4.3.2. v0.6.4
causes immediate failures in `usher-sampled` when called with the `-D` flag against
pangolin-data v1.38's `lineageTree.pb`.

The 2026-04-23 fix correctly bumped pangolin-data to v1.38 but inadvertently kept UShER
at v0.6.4 — a version known broken since pangolin v4.3.2 (released before our v4.3.4
upgrade).

## Fix

`src/backend/Dockerfile.pangolin` line 21:
```diff
-RUN cd usher && git checkout v0.6.4 && ./install/installUbuntu.sh
+RUN cd usher && git checkout v0.6.3 && ./install/installUbuntu.sh
```

v0.6.3 is the last safe version for pangolin 4.3.x + pangolin-data 1.38.

## Production Deployment

| Parameter | Before | After |
|---|---|---|
| `/genepi/geprod/geprodstack/pangolin-sfn` | `sha-5ab2c64` | `sha-90d8e8f` |
| `/genepi/geprod/geprodstack/pangolin-ondemand-sfn` | `sha-5ab2c64` | `sha-90d8e8f` |

Backend ECS restarted (`geprodstack-backend`, force-new-deployment).  
Terraform revert protected: `image_tags = { pangolin = "sha-90d8e8f" }` added to
`.happy/terraform/envs/prod/main.tf`.

## Backups

- `ssm-backups/geprod-pangolin-sfn-2026-04-24.json`
- `ssm-backups/geprod-pangolin-ondemand-sfn-2026-04-24.json`

---

# Change Log — 2026-04-24: TBB 2020.3 Library Fix (confirmed fix)

**Author**: Thanh Lee  
**Related issue**: [#28](https://github.com/theiagenghi/theiagenepi/issues/28) (follow-up)  
**Branch**: `tghi-docker-pangolin-bookworm`  
**New pangolin image**: `654654542669.dkr.ecr.us-west-2.amazonaws.com/genepi-pangolin:sha-2f4c907`  
**Test execution**: `KE-JROTIENO-tbb-fix-2026-04-24-15-00` — **SUCCEEDED**

## Problem

After the UShER v0.6.3 downgrade (`sha-90d8e8f`), pangolin on-demand jobs still failed in
`usher_inference`. The job exited within ~1 second with no useful output — all `usher-sampled`
output is redirected to a log inside Snakemake's tmpdir, which Snakemake deletes on exit
before any EXIT trap can read it.

## Diagnosis Approach

Added pre-emptive ldd and binary invocation test to `run_pangolin.sh` *before* pangolin
runs — so any dynamic linker errors appear in CloudWatch before Snakemake's tmpdir cleanup.

Image `sha-1fea506` (with ldd diagnostics) revealed:
```
usher-sampled: error while loading shared libraries: libtbb_preview.so.2: cannot open shared object file
```

Also missing: `libmpi.so.40`, `libmpi_cxx.so.40`, `libisal.so.2`.

## First Fix Attempt (sha-244024c) — Symbol Lookup Error

Added `libopenmpi3` + `libisal2` via apt. Created symlink:
```
libtbb_preview.so.2 → /usr/lib/x86_64-linux-gnu/libtbb_preview.so.12
```

This resolved the "file not found" error but caused a new failure:
```
usher-sampled: symbol lookup error: undefined symbol: _ZTIN3tbb4taskE
```
(`typeinfo for tbb::task` — removed in oneTBB 2021.x)

## Root Cause

UShER's `installUbuntu.sh` uses CMake FetchContent to build TBB 2020.3 from source at:
```
/usherbuild/usher/build/tbb_cmake_build/tbb_cmake_build_subdir_release/libtbb_preview.so.2
```
This TBB 2020.3 library has `tbb::task`. The Dockerfile multi-stage build only COPYed the
UShER binaries, leaving behind the TBB library in the build stage. Bookworm's `libtbb12`
package is oneTBB 2021.x which removed `tbb::task` — so symlinking `.so.2 → .so.12`
provides the filename but not the required symbol.

## Real Fix (sha-2f4c907)

```dockerfile
# Copy TBB 2020.3 libtbb_preview.so.2 built by UShER's CMake FetchContent.
# This provides tbb::task (removed in oneTBB 2021.x) which usher-sampled requires.
COPY --from=usher \
    /usherbuild/usher/build/tbb_cmake_build/tbb_cmake_build_subdir_release/libtbb_preview.so.2 \
    /usr/lib/x86_64-linux-gnu/libtbb_preview.so.2
RUN ldconfig
```

The `libtbb_preview.so.12` symlink remains (needed because usher-sampled's NEEDED entry
lists `.so.12`, which the dynamic linker resolves first to our `.so.12` symlink pointing
to the system `libtbb.so.12` for non-preview tbb symbols, while `libtbb_preview.so.2` is
the actual implementation providing `tbb::task`).

## Production Deployment

| Parameter | Before | After |
|---|---|---|
| `/genepi/geprod/geprodstack/pangolin-sfn` | `sha-244024c` | `sha-2f4c907` |
| `/genepi/geprod/geprodstack/pangolin-ondemand-sfn` | `sha-244024c` | `sha-2f4c907` |

Backend ECS restarted (`geprodstack-backend`, force-new-deployment).  
Terraform revert protected: `image_tags = { pangolin = "sha-2f4c907" }` in
`.happy/terraform/envs/prod/main.tf`.

## Backups

- `ssm-backups/geprod-pangolin-sfn-2026-04-24e.json` (before sha-2f4c907 deploy)
- `ssm-backups/geprod-pangolin-ondemand-sfn-2026-04-24e.json` (before sha-2f4c907 deploy)

## Cleanup

Diagnostic ldd/binary test block removed from `run_pangolin.sh` (commit `3789bc5c`).
Pushed — no Docker rebuild needed; the production image `sha-2f4c907` is confirmed working.
