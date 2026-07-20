# Auth0 Replacement — Current-State Findings

**Date:** 2026-07-13
**Scope:** `theiagenepi` backend (`src/backend`), CLI (`src/cli`), frontend (`src/frontend`), and infra (`.happy`, `.github/workflows`).
**Purpose:** Establish, from the code as it actually is today, exactly what Auth0 does for this application, what is already decoupled from it, and what remains bound to it. This is the factual base for [`02-options-and-recommendation.md`](./02-options-and-recommendation.md) and [`03-implementation-and-migration-plan.md`](./03-implementation-and-migration-plan.md).

Every claim below is grounded in a specific file and line. Where a fact contradicts the older `backend/aspen/SPLIT_IO_AND_AUTH0_ANALYSIS.md`, this document (freshly verified against the working tree) supersedes it.

---

## TL;DR

Auth0 does **three** distinct jobs in this app, but only two of them are still load-bearing:

1. **Authentication** (login handshake + CLI token validation) — *still Auth0-bound*, but through a **standard OIDC** interface that already runs against a non-Auth0 provider in local dev and CI.
2. **Provisioning** (create orgs, invite/add members, assign roles, delete invitations) via the Auth0 **Management API** — *still Auth0-bound*, and this is the **largest and least-portable** surface.
3. **Authorization** (who-can-do-what at request time) — **already fully local.** Roles live in PostgreSQL and are queried directly; Auth0 is not consulted during normal request authorization, and the login-time role-sync is commented out.

The practical consequence: **the replacement problem is smaller than "rip out Auth0" implies.** Authorization is done. The frontend has zero Auth0 coupling. The login flow is already OIDC-generic (proven locally). What genuinely needs building is (a) a provisioning path that no longer calls the Auth0 Management API, and (b) a **local store for invitations**, which today exist *only* inside Auth0.

---

## 1. The three jobs Auth0 does here

| Job | Mechanism | Portability today |
|---|---|---|
| **Authentication — browser login** | `authlib` `StarletteOAuth2App`, standard OIDC authorization-code flow (`views/auth.py`) | **High** — any OIDC provider works; proven against a self-hosted mock in dev/CI |
| **Authentication — CLI** | OAuth 2.0 Device Authorization Grant (RFC 8628) + RS256 JWT verified via JWKS (`cli/aspencli.py`, `auth/device_auth.py`) | **High** — already has a working non-Auth0 `local` config |
| **Provisioning** | Auth0 **Management API** via `auth0-python` SDK (`auth/auth0_management.py`) — orgs, members, roles, invitations | **Low** — bespoke to Auth0's Organizations product; no local equivalent for invitations |
| **Identity storage (join keys)** | `User.auth0_user_id`, `Group.auth0_org_id` — opaque unique strings in PostgreSQL | **High** — opaque columns, no format enforced |
| **Authorization** | Local `Role`/`UserRole`/`GroupRole` tables + Oso `policy.polar` | **Already local — no Auth0 involvement** |

---

## 2. What is already decoupled (the good news)

This is the most important — and most underappreciated — part of the picture. A large fraction of the "Auth0 migration" is already done.

### 2.1 Authorization is 100% local

- Roles are a hardcoded three-value set — `admin`, `viewer`, `member` — seeded into PostgreSQL by migration `20220616_232147` (lines 57–58) and by `conftest.py:95`, and referenced by `policy.polar`. They are **not** fetched from Auth0.
- Request-time authorization reads local tables only. `get_auth_context` / `get_user_roles` / `require_group_membership` (`authn.py:202–278`) query `UserRole` / `GroupRole` directly. **Zero Auth0 calls happen during normal request authorization.**
- The login-time Auth0→DB role sync **is commented out** in the `/callback` handler (`views/auth.py:224–229`, an uncommitted working-tree change). It survives only in `/process_invitation` (`views/auth.py:296–298`), gated behind the Split.io flag `sync_auth0_roles`.

**Implication:** Auth0 is no longer the source of truth for authorization. The `Role` model is already provider-agnostic.

### 2.2 The frontend has zero Auth0 coupling

- No `@auth0/*` SDK anywhere in `src/frontend` (`rg -ni "auth0|oauth" frontend/package.json` → nothing).
- Every auth action is a **full-page redirect to a backend route**: `Sign in` → `{API_URL}/v2/auth/login`, logout → `{API_URL}/v2/auth/logout` (`common/api/index.tsx:6–9`, `NavBar/.../UserMenu/index.tsx:57`).
- "Am I logged in?" is a single cookie-authenticated call: `GET /v2/users/me` with `credentials: "include"` (`common/queries/auth.ts:40–42`, `common/api/index.tsx:48–50`). No client-side token, ever.

**Implication:** replacing Auth0 requires **no frontend code changes** as long as `/v2/auth/login`, `/v2/auth/logout`, and the session cookie behind `/v2/users/me` keep working.

### 2.3 The CLI is already IdP-agnostic

`cli/aspencli.py` implements the OAuth 2.0 Device Authorization Grant against a per-environment config table (`CliConfig`, lines 223–268). It already ships a **non-Auth0 `local` profile** pointing at a generic OIDC server:

```python
"local": {
    "auth_url": "https://oidc.genepinet.localdev:8443",
    "client_id": "local-client-id",
    "verify": False,
    "oauth_api_config": {
        "device_auth_url": "{auth_url}/connect/deviceauthorization",
        "poll_url": "{auth_url}/connect/token",
        "jwks_url": "{auth_url}/.well-known/openid-configuration/jwks",
        ...
    },
},
```

The abstraction is already the right shape: give it RFC-8628 device-auth endpoints + a JWKS URL and it works, Auth0 or not.

> **Caveat:** the CLI's own unit tests (`cli/tests/`) are **stale and do not pass** — call signatures drifted (`get_api_client()` now requires `org_id`/`pathogen_slug`; `TokenHandler`/`ApiClient` gained required args) and `cli/requirements.txt` omits `mock`/`requests`/`requests_mock`. So there is currently **no passing automated coverage of the device-auth flow**. Any migration touching the CLI should budget to repair these first.

### 2.4 Local dev and CI already run on a self-hosted OIDC server

This is the pivotal finding: **self-hosted OIDC is not hypothetical here — it is the current reality in every non-production environment.**

- `docker-compose.yml:131–161` runs [`ghcr.io/soluto/oidc-server-mock`](https://github.com/Soluto/oidc-server-mock) (an IdentityServer4-based ASP.NET mock IdP) at `oidc.genepinet.localdev`, served over HTTPS (`8443`) with a generated self-signed cert trusted into the OS keychain (`Makefile:68–88`).
- `scripts/setup_dev_data.sh:58–81` writes the **entire `genepi-config` secret** — same keys as the real staging/prod secret — into localstack Secrets Manager, pointed at the mock.
- The backend's `oauth.register(...)` (`main.py:88–96`), the CLI's device-code flow, and `device_auth.py`'s JWKS signature verification **all run unmodified** against this mock. Only one conditional distinguishes it from real Auth0: `if "genepinet.localdev" in domain` (`device_auth.py:47`), which switches to IdentityServer's JWKS path (`/.well-known/openid-configuration/jwks`) and disables TLS verification for the self-signed cert.
- CI exercises it too: `push-tests.yml` runs `make local-init LOCALDEV_PROFILE=backend`, which brings up `oidc` and wires the backend to it before running the pytest suite.

**Implication:** the claim "this codebase can authenticate against a non-Auth0 OIDC provider" is already **proven in code and CI** — for the *login* path. (The Management API path is *not* proven this way; see §3.3 and the gotcha in §2.5.)

### 2.5 Terraform and CI are Auth0-agnostic

- Exhaustive search of `.happy/**/*.{tf,tfvars,json,yml,yaml}` for `auth0|oidc|oauth`: **zero matches.** Terraform treats the whole `genepi-config` secret as an opaque blob and only ever passes its *name* (`${stage}/genepi-config`) into the container (`modules/service/main.tf:6`, `modules/ecs-stack/main.tf:8–17`).
- No `.github/workflows/*.yml` references `AUTH0`/`OIDC`/`OAUTH` directly.

**Implication:** swapping IdPs needs **no Terraform change to the secret's plumbing** — you change the secret's *contents* and stand up one new service. The "next `terraform apply` reverts my change" risk documented for the pangolin SSM work does **not** apply to the secret payload here.

> **The matching gotcha:** the local mock's `AUTH0_MANAGEMENT_CLIENT_ID/SECRET/DOMAIN` are literally the string `"update_me"` (`setup_dev_data.sh:51–53`) because the mock has **no Management API**. So local/CI prove the *login* flow against a self-hosted IdP but have **never** exercised the *provisioning* flow (orgs/invitations/roles) against anything but real Auth0. That untested surface is exactly where the migration risk concentrates.

---

## 3. What is still Auth0-bound

### 3.1 Browser login handshake (`views/auth.py`)

Standard OIDC authorization-code flow via `authlib`:

- `/login` → `oauth.authorize_redirect(...)` → provider `/authorize` (`views/auth.py:112–146`).
- `/callback` → `oauth.authorize_access_token(request)` → reads `token["userinfo"]` → `create_user_if_not_exists` → sets `request.session["profile"]["user_id"] = userinfo["sub"]` (`views/auth.py:187–249`).
- `/logout` → clears session → redirects to `settings.AUTH0_LOGOUT_URL` (`views/auth.py:320–332`).

The only Auth0-specific pieces are the **provider URLs** (all derived from `AUTH0_DOMAIN` or the `SECRET_AUTH0_*` overrides in `settings.py:242–281`) and the **claim names** it reads: `sub`, `email`, `name`, and — for invitation-driven signup — `org_id`.

> **`org_id` is an Auth0-Organizations artifact.** `create_user_if_not_exists` (`views/auth.py:149–184`) requires `org_id` in the userinfo to link a brand-new user to a `Group`. But the local OIDC mock emits **no `org_id`** (`oauth/users.json` has only `name` + `email`) — local dev works because the user's group membership is pre-seeded in the DB. This confirms `org_id` is not fundamental; an invitation record that carries its own `group_id` removes the need for it entirely.

### 3.2 CLI token validation (`auth/device_auth.py`)

`validate_auth_header(auth_header, domain, client_id)` verifies an RS256 JWT's signature against the provider's JWKS and checks issuer/audience, using `auth0.v3`'s `AsymmetricSignatureVerifier` + `TokenVerifier`. Called from `get_token_userid` (`authn.py:118–131`) with `settings.AUTH0_DOMAIN` / `settings.AUTH0_CLIENT_ID`.

Auth0-specific only in: the `auth0.v3` **library import** (a JWT/JWKS helper — replaceable with any JOSE library) and the domain/JWKS-path convention. The token *contents* it needs are just a signed `sub` claim.

### 3.3 Provisioning via the Auth0 Management API (`auth/auth0_management.py`) — the big one

`Auth0Client` wraps `auth0-python` v3 (synchronous, `requests`-based). Constructed fresh **per request** by `get_auth0_apiclient` (`authn.py:175–180`) — each construction does a client-credentials token exchange, a real performance smell in an async app. Call sites that actually matter:

| Endpoint / caller | Management API calls | What it does |
|---|---|---|
| `POST /groups/` (`views/groups.py`) | `add_org` | Create an Auth0 Organization when a `Group` is created |
| `POST /groups/{id}/invitations/` (`views/groups.py:127`) | `invite_member` | Create an Auth0 org invitation (email + roles) |
| `GET /groups/{id}/invitations/` (`views/groups.py`) | `get_org_invitations` | List pending invitations |
| `/login` invitation branch (`views/auth.py:40`) | `get_org_invitations` | Look up a ticket by id (can't query tickets directly) |
| `/process_invitation` (`views/auth.py:291–293`) | `add_org_member`, `delete_organization_invitation` | Accept invite: add member + roles, delete ticket |
| `/process_invitation` role-sync (`views/auth.py:296–298`) | `get_user_orgs`, `get_org_user_roles` (via `RoleManager`) | Split-gated Auth0→DB role sync |
| `PUT /users/me` (`views/users.py`) | `update_user` | Update display name in Auth0 |

The `Auth0Client` surface also includes `remove_org_member`, `add/remove_org_roles`, `delete_org`, `create_user`, `delete_user`, `get_org_members`, `get_roles`, `get_connections`, and a hardcoded `"Username-Password-Authentication"` connection. It is used most heavily by the standalone bulk-sync CLI `aspen/cli/sync_auth0.py` (invoked manually; not wired into an entrypoint) and by `aspen/auth/role_manager.py` (`sync_user_roles`, Auth0→DB, one-directional).

**This is the surface with no portable equivalent.** Every other coupling is a URL or a claim name; this is a bespoke integration against Auth0's Organizations product.

### 3.4 Schema join keys (`database/models/usergroup.py`)

- `User.auth0_user_id = Column(String, unique=True, nullable=False)` (line 94) — the **sole identity anchor** for a user. `email` is stored and unique but is *not* the login key.
- `Group.auth0_org_id = Column(String, unique=True, nullable=False)` (line 50) — the sole org anchor.
- Both are **opaque strings with no format `CHECK`**. Test factories freely set values like `"User1"` that look nothing like real Auth0 ids. The only place a specific id *shape* is load-bearing is the (currently disabled) `user.auth0_user_id.startswith("auth0|")` guard in `views/auth.py`.

**Implication:** a new provider's `sub` can be stored in `auth0_user_id` with **no schema change**. Renaming the columns to `idp_user_id` / `idp_org_id` is cleaner but strictly optional and can be deferred.

### 3.5 There is NO local invitations table

Invitations exist **entirely inside Auth0** as Organization Invitations. There is no `Invitation` model, no invitations table, nothing in `usergroup.py` or the migrations. The flow is: `invite_member` (create ticket in Auth0) → email link → `/login` looks the ticket up via `get_org_invitations` → `/process_invitation` calls `add_org_member` + `delete_organization_invitation`.

**Implication:** this is the single largest **net-new build** in any replacement — not a re-point of an existing store, but a store that doesn't exist yet (model, endpoints, email, token/expiry semantics).

### 3.6 Two parallel config systems both read Auth0 from one secret

Both pull Auth0 values from the same AWS Secrets Manager secret (`${stage}/genepi-config`, env `GENEPI_CONFIG_SECRET_NAME`):

- **System A (live FastAPI):** `aspen/api/settings.py` — Pydantic `BaseSettings`. Loader chain ends in `aws_secret_settings` (localstack-aware via `BOTO_ENDPOINT_URL`). Auth0 fields at lines 102–121; computed provider URLs at 242–281.
- **System B (legacy, still imported):** `aspen/config/config.py` + `production.py`/`docker_compose.py`/`testing.py`. Still imported by `database_migrations/env.py`, `scripts/setup_localdata.py`, `aspen/cli/db.py`, `aspen/cli/sync_auth0.py`, `aspen/database/connection.py`, and ~20 `aspen/workflows/*.py`.

**Implication:** a config change must be made in **both** systems, or CLI/migrations/workflows will diverge from the live app. (Minor real inconsistency already exists: `APISettings` computes the callback as `/v2/auth/callback` while the legacy configs and `setup_dev_data.sh` use `/callback`; the mock registers both.)

---

## 4. Request-time authentication: three paths, one anchor

`get_auth_user` (`authn.py:140–167`) resolves a `User` by trying, in order:

1. **Magic link** (`magic_link_userid`, `authn.py:110–115`) — an Auspice HMAC-SHA3-512 link keyed by `AUSPICE_MAC_KEY`. **Not Auth0.** Survives any IdP swap untouched.
2. **Session cookie** (`get_cookie_userid`, `authn.py:134–137`) — reads `request.session["profile"]["user_id"]`, set at `/callback`. The cookie itself is a Flask-compatible signed cookie (custom `SessionMiddleware`, `itsdangerous`, 14-day, `SameSite=Lax`, secret = `FLASK_SECRET`) — **not** a JWT, **not** Auth0-issued. Provider-agnostic.
3. **Bearer JWT** (`get_token_userid`, `authn.py:118–131`) — the CLI path; validates against the provider JWKS (§3.2).

All three resolve to the same thing: a `User` row found by an opaque `auth0_user_id` string. **No Auth0-specific structure is enforced at the request-auth layer.**

The test suite injects auth at exactly this seam: `conftest.py` overrides `get_cookie_userid` with a function that reads a plain `user_id` HTTP header (`conftest.py:129–163`), and autospec-mocks both `Auth0Client` and `StarletteOAuth2App`. So tests never touch real Auth0 — meaning **an IdP swap barely disturbs the test infrastructure**, provided the new provisioning adapter keeps the same method shapes the mocks expect (the `Auth0Org`/`Auth0User`/`Auth0OrgInvitation` TypedDicts in `auth0_management.py:10–51`).

---

## 5. Why replace it: cost and migration constraints (external, web-verified)

### 5.1 This app pays Auth0's B2B rates, not the hobby free tier

The app uses Auth0 **Organizations** (orgs = `Group`s, with org-scoped invitations and roles). Organizations is a **B2B** feature. Per [Auth0 pricing](https://auth0.com/pricing) (verified 2026-07-13):

- The generic free tier tops out around **7,500 MAU** and does **not** include B2B Organizations at scale.
- **B2B Essentials starts at ~$150/mo (500 MAU); B2B Professional ~$800/mo (1,000 MAU)**, with **$0.07/MAU** overage beyond the base.
- SAML enterprise connections force the Professional tier regardless of MAU.

So the exact capability this codebase is most coupled to (org-scoped invitations + roles) is the one Auth0 bills at B2B rates. The cost argument is stronger than a generic "Auth0 got expensive."

### 5.2 Migrating users OFF Auth0 cannot take the password hashes (easily)

Per Auth0's own migration docs and multiple corroborating sources (verified 2026-07-13): Auth0 does **not** include password hashes in the standard bulk user export. Retrieving them requires a **support ticket**, is effectively **Enterprise-gated**, and can take **a week or more**.

**Implication for "migrate real users":** unless the org opens that ticket and waits, the migration **cannot** carry passwords. The realistic strategies are **password-reset-on-first-login** or **lazy/trickle migration** — never a silent hash copy. This is a first-class constraint in [`03-implementation-and-migration-plan.md`](./03-implementation-and-migration-plan.md).

> Note: this constraint is about *credential* portability. The **identity records** (email, name, org membership, roles) are fully in our own PostgreSQL already and migrate trivially — only the secret (the password) is stuck.

---

## 6. Coupling scorecard

Ranked by effort to replace, hardest first:

| Surface | Where | Portability | Replacement effort |
|---|---|---|---|
| **Invitations** (no local store) | Auth0 Org Invitations API | None — must be built | **High** (net-new model + endpoints + email + tokens) |
| **Provisioning** (orgs/members/roles) | `auth/auth0_management.py` + call sites | Low — bespoke to Auth0 | **High** (new adapter or move fully local) |
| **Login handshake** | `views/auth.py` + `settings.py` URLs | High — standard OIDC | **Low** (config + proven local flow) |
| **CLI JWT validation** | `auth/device_auth.py`, `aspencli.py` | High — standard JWKS/device flow | **Low** (config; repair stale tests) |
| **Config wiring** | `settings.py` + `config/config.py` (×2 systems) | High | **Low–Medium** (touch both systems) |
| **Schema join keys** | `auth0_user_id`, `auth0_org_id` | High — opaque strings | **None required** (optional rename later) |
| **Frontend** | redirects to backend routes | Total — zero coupling | **None** |
| **Authorization** | local role tables + Oso | Already local | **None — done** |
| **Terraform / CI** | opaque secret name only | Total — zero coupling | **None** |

The center of gravity is unambiguous: **invitations + provisioning**. Everything else is either already portable or already done.

---

## Appendix — file reference index

| Concern | File(s) |
|---|---|
| Login/callback/logout/invitation flow | `aspen/api/views/auth.py` |
| Request auth (cookie/JWT/magic-link) + DI | `aspen/api/authn.py`, `aspen/api/deps.py` |
| CLI JWT/JWKS validation | `aspen/auth/device_auth.py` |
| Auth0 Management API wrapper | `aspen/auth/auth0_management.py` |
| Auth0→DB role sync | `aspen/auth/role_manager.py`, `aspen/cli/sync_auth0.py` |
| Config (live) | `aspen/api/settings.py` |
| Config (legacy) | `aspen/config/config.py`, `config/{production,docker_compose,testing}.py` |
| App factory / OAuth registration / session | `aspen/api/main.py`, `aspen/api/middleware/session.py` |
| Schema (identity join keys, roles) | `aspen/database/models/usergroup.py` |
| Test seams | `aspen/api/conftest.py`, `aspen/api/views/tests/data/auth0_mock_responses.py` |
| CLI | `cli/aspencli.py`, `cli/tests/` (stale) |
| Frontend touchpoints | `frontend/src/common/api/index.tsx`, `common/queries/auth.ts`, `components/NavBar/**` |
| Local OIDC server | `docker-compose.yml:131–161`, `oauth/`, `scripts/setup_dev_data.sh` |
| Infra (Auth0-agnostic) | `.happy/terraform/modules/{service,ecs-stack,migration,deletion,batch}/**` |
