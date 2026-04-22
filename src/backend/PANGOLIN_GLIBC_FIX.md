# Pangolin / UShER Version Stuck at v1.34

**Issue**: [#28 — UShER/pUShER version remains at v1.34 for recent uploads (expected ≥ v1.37)](https://github.com/theiagenghi/theiagenepi/issues/28)

---

## What users see

Samples uploaded in early 2026 show `PUSHER-v1.34` in the lineage output, even though
pangolin-data v1.37 was released in January 2026 and v1.38 is now the latest. Newer
lineage definitions are not incorporated, affecting downstream lineage assignments and
counts.

```
Private ID   | Lineage    | Version      | Lineage Caller
OZ365056.1   | BA.3.2.2   | PUSHER-v1.34 | PANGOLIN
OZ373472.1   | BA.3.2.2   | PUSHER-v1.34 | PANGOLIN
```

The `PUSHER-v<X>` string is sourced directly from the `pangolin-data` Python package
installed in the container — it is **not** the UShER binary version.

---

## Root Cause

### 1. The glibc breakage in `Dockerfile.pangolin`

`Dockerfile.pangolin` uses two Debian Bullseye base images:

```dockerfile
FROM debian:bullseye AS usher   # glibc 2.31
...
FROM python:3.10-slim-bullseye AS base   # glibc 2.31
```

The UShER build stage compiles `faToVcf` and other UShER tools from source
(`git checkout v0.6.4`). As of roughly March 2025, the compiled UShER binaries started
requiring `GLIBC_2.33` and `GLIBC_2.34`, which Bullseye (glibc 2.31) cannot satisfy.
The result is a runtime crash:

```
/usr/local/bin/faToVcf: /lib/x86_64-linux-gnu/libc.so.6: version 'GLIBC_2.33' not found
/usr/local/bin/faToVcf: /lib/x86_64-linux-gnu/libc.so.6: version 'GLIBC_2.34' not found
```

This means **any image built from the in-repo `Dockerfile.pangolin` is broken at
runtime**. The normal CI/CD path (push to a `tghi-docker-*` branch →
`build-and-push-ecr.yml`) cannot produce a working pangolin image.

### 2. The `docker-pangolin-fix` workaround

To work around the broken build pipeline, a separate repo
([theiagenghi/docker-pangolin-fix](https://github.com/theiagenghi/docker-pangolin-fix))
was created. Rather than rebuilding from source, it layers a `pangolin-data` update on
top of a known-working ECR image:

```dockerfile
FROM 654654542669.dkr.ecr.us-west-2.amazonaws.com/genepi-pangolin:sha-e0efd43

RUN pip3 install git+https://github.com/cov-lineages/pangolin-data.git@v1.34
```

The resulting image was pushed manually from an EC2 instance (with the `EC2-ECR` IAM
role) and tagged as `sha-713e6f8`. Both ECS Step Function configs — `pangolin-sfn` and
`pangolin-ondemand-sfn` — were then hard-coded to this SHA in the Terraform locals:

```hcl
# .happy/terraform/modules/ecs-stack/main.tf
pangolin_image = join(":", [local.secret["ecrs"]["pangolin"]["url"],
                            lookup(var.image_tags, "pangolin", var.image_tag)])
```

### 3. Why the version is frozen at v1.34

The `docker-pangolin-fix` Dockerfile only ever bumped `pangolin-data` to `v1.34`.
Because the hard-coded image SHA never changed after that, and the normal CI/CD build
pipeline remains broken, no subsequent pangolin-data releases (`v1.35`, `v1.36`,
`v1.37`, `v1.38`) have reached production.

```
pangolin-data releases:  v1.31 → v1.34 → v1.35 → v1.36 → v1.37 → v1.38 (latest)
                                   ↑
                         stuck here since the fix
```

### Dependency versions currently in production

| Component        | Pinned in repo       | In prod image (sha-713e6f8) |
|-----------------|----------------------|-----------------------------|
| pangolin         | v4.3.1               | v4.3.1                      |
| pangolin-data    | v1.31                | v1.34                       |
| scorpio          | v0.3.19              | v0.3.19                     |
| constellations   | v0.1.12              | v0.1.12                     |
| UShER            | v0.6.4 (from source) | v0.6.4                      |
| snakemake        | 7.30.1               | 7.30.1                      |
| Base OS          | debian:bullseye      | bullseye (glibc 2.31)       |

---

## Solution

### Track 1 — Immediate hotfix (same as docker-pangolin-fix pattern)

Build a new image from the current working production image, upgrading only
`pangolin-data` to `v1.38`:

```dockerfile
FROM 654654542669.dkr.ecr.us-west-2.amazonaws.com/genepi-pangolin:sha-713e6f8

RUN pip3 install git+https://github.com/cov-lineages/pangolin-data.git@v1.38 \
                 git+https://github.com/cov-lineages/scorpio.git@v0.3.19 \
                 git+https://github.com/cov-lineages/constellations.git@v0.1.12
```

**Deploy steps** (on an EC2 with the `EC2-ECR` IAM role):

```bash
aws ecr get-login-password --region us-west-2 \
  | docker login --username AWS --password-stdin 654654542669.dkr.ecr.us-west-2.amazonaws.com

docker build -t 654654542669.dkr.ecr.us-west-2.amazonaws.com/genepi-pangolin:<new-sha> \
  -f docker-pangolin-fix/Dockerfile .

docker push 654654542669.dkr.ecr.us-west-2.amazonaws.com/genepi-pangolin:<new-sha>
```

Then update `image_tags["pangolin"]` in the Terraform stack to `<new-sha>` and redeploy.

**Risk**: Low — identical pattern to what already works in production.  
**Limitation**: Does not fix the broken CI/CD build pipeline; manual EC2 process still required for future updates.

---

### Track 2 — Proper fix (restores the CI/CD pipeline)

Upgrade the base OS in `Dockerfile.pangolin` from Debian Bullseye (glibc 2.31) to
Bookworm (glibc 2.36). This resolves the `GLIBC_2.33`/`GLIBC_2.34` requirement and
allows the image to be built and pushed through the normal `build-and-push-ecr.yml`
workflow.

#### Changes to `Dockerfile.pangolin`

```diff
-FROM debian:bullseye AS usher
+FROM debian:bookworm AS usher

-FROM python:3.10-slim-bullseye AS base
+FROM python:3.10-slim-bookworm AS base

 RUN apt-get update && apt-get install -y  \
         make wget git jq gcc unzip curl bzip2 \
         libboost-filesystem1.74.0 \
         libboost-program-options1.74.0 \
         libboost-iostreams1.74.0 \
         libboost-date-time1.74.0 \
-        libprotobuf23 \
-        libtbb2
+        libprotobuf32 \
+        libtbb12

-RUN ln -s /usr/lib/x86_64-linux-gnu/libtbb.so.2 /usr/lib/x86_64-linux-gnu/libtbb_preview.so.2
+RUN ln -s /usr/lib/x86_64-linux-gnu/libtbb.so.12 /usr/lib/x86_64-linux-gnu/libtbb_preview.so.12

-RUN pip3 install git+https://github.com/cov-lineages/pangolin.git@v4.3.1
-RUN pip3 install git+https://github.com/cov-lineages/pangolin-data.git@v1.31
+RUN pip3 install git+https://github.com/cov-lineages/pangolin.git@v4.3.4
+RUN pip3 install git+https://github.com/cov-lineages/pangolin-data.git@v1.38
```

`scorpio@v0.3.19` and `constellations@v0.1.12` are already at the latest available
releases and do not need to be changed.

> **UShER version note**: `pangolin v4.4` tightens the UShER constraint to `<=0.6.3`,
> which would conflict with the current `v0.6.4` build. Stay on `pangolin v4.3.4` to
> avoid a UShER source downgrade. If upgrading to v4.4 in the future, also change the
> UShER git checkout from `v0.6.4` to `v0.6.3` in the first build stage.

#### Deploy steps

```bash
# Push to a tghi-docker-* branch — CI/CD handles the ECR push automatically
git checkout -b tghi-docker-pangolin-bookworm
# (apply Dockerfile.pangolin changes)
git push origin tghi-docker-pangolin-bookworm
# build-and-push-ecr.yml runs; new image is tagged with the commit SHA
```

Then update `image_tags["pangolin"]` to the new SHA in Terraform and redeploy.

---

## Recommended approach

1. **Do Track 1 first** — unblocks users immediately; takes hours, not days.
2. **Do Track 2 in parallel or immediately after** — removes the manual EC2 workaround
   forever and closes the outstanding TODO in `docker-pangolin-fix`:
   > `[ ] upgrade base docker image to newer release, i.e. bookworm or trixie`
