# Auth0 Replacement — Specification

**Date:** 2026-07-20
**Branch:** `tghi-auth0-replacement` (off `origin/tghi-dev`)
**Reads on:** [`01-current-state-findings.md`](./01-current-state-findings.md), [`02-options-and-recommendation.md`](./02-options-and-recommendation.md), [`03-implementation-and-migration-plan.md`](./03-implementation-and-migration-plan.md)
**Feeds:** [`05-implementation-plan.md`](./05-implementation-plan.md)

This is the buildable specification for the recommended option (Tier B): **a self-hosted OIDC provider for authentication only, with organizations, roles, and invitations owned by our PostgreSQL.**

Where doc 03 sketched phases, this document fixes the contracts: what each component is, what data it holds, which API shapes are frozen, and what "done" means per phase.

---

## 1. Scope

### In scope (code, this branch)

| Phase | Deliverable |
|---|---|
| **0** | `IdentityProvider` seam; Auth0 becomes one implementation behind it; provider built once at startup instead of per request |
| **1** | OIDC config generalized so a Keycloak swap is config-only (removes the hardcoded `genepinet.localdev` branch) |
| **2** | `Invitation` table + local provisioning; group create / invite / accept / profile-update stop calling the Auth0 Management API |
| **2e** | **Email delivery** — new, because Auth0 was sending invitation emails for us |
| **3′** | A user-migration script (executable by ops, not run here) |

### Out of scope (not code, or not this branch)

- Deploying Keycloak, configuring realms/clients, or changing Terraform. Login is a config swap; standing up the service is ops work.
- Executing the production user migration or password-reset campaign.
- Renaming `auth0_user_id` → `idp_user_id` (deliberately deferred; see §7.1).
- Frontend changes. §5 freezes the API shapes precisely so none are needed.
- Repairing the stale CLI test suite (findings §2.3) — tracked, but orthogonal.

### The one hard constraint

**The frontend must not change.** Every API response shape it consumes is frozen in §5. This is both a scope control and a correctness check: if a change would force a frontend edit, the design is wrong.

---

## 2. Architecture

Three components, each with one job:

```
                    ┌─────────────────────────┐
  login/callback ──►│  OIDC provider (config) │  Keycloak / Auth0 / mock
  CLI device flow   │  authenticate only      │  — swappable, no app logic
                    └─────────────────────────┘
                                 │ sub, email, name
                                 ▼
                    ┌─────────────────────────┐
  groups / users ──►│   IdentityProvider      │  Phase 0 seam
  invitations       │   (protocol)            │
                    └───────────┬─────────────┘
                        ┌───────┴────────┐
                        ▼                ▼
                 Auth0Provider     LocalProvisioning   ◄── Phase 2 target
                 (existing, kept    (PostgreSQL:
                  until cutover)     Group / UserRole /
                                     Invitation)
                                             │
                                             ▼
                                    ┌─────────────────┐
                                    │  EmailSender    │  Phase 2e
                                    │  SES | console  │
                                    └─────────────────┘
```

### 2.1 `IdentityProvider` (Phase 0)

A protocol capturing only the provisioning methods actually called today. It exists so Phase 2 can swap implementations without touching call sites, and so every phase stays green.

Methods, **as built** (`aspen/auth/identity_provider.py`):

```
async create_org(group_prefix, group_name)              -> str
async list_invitations(org_id)                          -> list[InvitationInfo]
async invite_member(org_id, invited_by, email, role)    -> None
async get_invitation(org_id, invitation_id)             -> InvitationInfo | None
async accept_invitation(org_id, invitation, user_id)    -> None
async update_user(user_id, **fields)                    -> None
```

This differs from the method list first sketched here. `get_org_by_id`, `delete_invitation` and `add_org_member` were dropped: every call site that would have used them is reachable through `get_invitation` / `accept_invitation`, and exposing the others would have committed the protocol to Auth0's decomposition of the flow rather than the application's.

**Data contract.** The protocol speaks in `InvitationInfo`, a flat TypedDict of what the API layer actually serialises — *not* Auth0's `Auth0OrgInvitation`. Each backend translates. The nested `inviter: {name}` / `invitee: {email}` shape the frontend depends on (§5) is reconstructed in `_serialize_invitation` in `api/views/groups.py`, so the frozen contract survives without leaking Auth0's payload shape into the core.

**Async, and therefore not behavior-neutral.** The protocol is async because the local backend reads and writes through an async SQLAlchemy session. The Auth0 adapter is a synchronous HTTP client, so it wraps its calls in `run_in_threadpool`. This means Phase 0 is *not* the purely behavior-neutral refactor originally claimed: it fixes a latent bug in which blocking Auth0 HTTP calls ran directly on the event loop, stalling every other in-flight request. That is a fix worth having, but it is a behavior change and is called out as one.

**Lifecycle.** The plan said "built once at startup." As built, it is constructed lazily on first use and cached on `app.state`. Startup construction was rejected because `Auth0Client.__init__` performs a client-credentials token exchange, and local dev and CI run with placeholder management credentials — eager construction would make the app fail to boot in exactly the environments the test suite runs in. The `local` backend is built per request regardless, because it holds that request's database session.

### 2.2 `LocalProvisioning` (Phase 2)

The same protocol, implemented against our own tables. No external calls.

| Protocol method | Local behavior |
|---|---|
| `create_org` | Insert `Group`; generate `auth0_org_id = "grp_" + uuid4().hex` to satisfy the existing NOT NULL UNIQUE column |
| `invite_member` | Insert `Invitation`; mint token, store only its hash; hand off to `EmailSender` |
| `list_invitations` | `SELECT ... WHERE group_id = ? AND status = 'PENDING' AND expires_at > now()` |
| `get_invitation` | Look up by token hash; return `None` unless still redeemable |
| `accept_invitation` | Set `status = 'ACCEPTED'`, stamp `accepted_at` (soft; preserves audit trail) |
| `update_user` | Update `User.name` locally only |

**Role granting is not a provider concern.** The draft table had `add_org_member` insert `UserRole`. That was wrong: it would have forced a database session into the Auth0 adapter, which has no business holding one. Authorization has lived in our own tables all along, so granting roles is identical no matter who issued the invitation. It happens once, in `grant_invitation_roles` (`api/views/auth.py`), which both the Auth0 and local paths call after `accept_invitation` returns.

This also removed `RoleManager.sync_user_roles`, which used to read the user's roles *back* out of Auth0 after accepting — a round-trip that existed only to reconcile two copies of the same truth, and a standing source of Auth0/database disagreement. The roles now come from the invitation itself.

### 2.3 `EmailSender` (Phase 2e — new, not in doc 03)

**Why this is a first-class component:** the backend has **no email capability today** — no SES client, no SMTP, no templating. Auth0 sent invitation emails via `send_invitation_email: True` (`auth0_management.py:277`). Owning invitations therefore means owning delivery. Doc 03 treated this as a clause ("send email with…"); it is a component.

Interface: `send_invitation(to, group_name, inviter_name, accept_url, expires_at)`.

Two implementations:

- **`SESEmailSender`** — production. `boto3` SES via the existing `Session` pattern in `settings.py`. Requires a verified sender identity and `ses:SendEmail` on the task role (an ops prerequisite, flagged in §8).
- **`ConsoleEmailSender`** — local dev, CI, and tests. Logs the message and accept URL. Default when SES is not configured, so **no test depends on network or AWS**.

Selection is by config, not by environment sniffing.

---

## 3. Data model

### 3.1 New table: `invitations`

```
invitations
  id            int PK
  group_id      int  NOT NULL  FK -> groups.id        (org being joined)
  email         str  NOT NULL                          (invitee; lowercased on write)
  role_id       int  NOT NULL  FK -> roles.id          (role granted on accept)
  token_hash    str  NOT NULL  UNIQUE                  (sha256 of raw token)
  invited_by_id int  NOT NULL  FK -> users.id
  created_at    timestamptz NOT NULL  default now()
  expires_at    timestamptz NOT NULL
  accepted_at   timestamptz NULL
  status        enum(pending, accepted, expired, revoked) NOT NULL default 'pending'

  INDEX (group_id, status)
  INDEX (email)
```

Design decisions and their reasons:

- **`token_hash`, never the raw token.** The raw token goes in the email and is never persisted — the same reason password hashes exist. A database leak must not yield usable invitations. Lookup is `sha256(token)` → indexed unique hit, replacing today's "list every Auth0 invitation and scan for a matching ticket id" (`views/auth.py:30–48`).
- **`role_id` FK, not a string array.** Roles are already a seeded three-value table (`admin`/`viewer`/`member`). A FK gets referential integrity for free; the Auth0 shape used a list only because Auth0's API did.
- **`status` enum rather than deriving from timestamps.** `expired` and `revoked` are distinct outcomes with different user-facing messages, and are not inferable from `accepted_at` alone.
- **Soft delete.** Revocation preserves the audit trail — who invited whom, when. Auth0's hard `delete_organization_invitation` destroyed that record.
- **Expiry default: 14 days**, matching the existing session cookie lifetime so the two windows don't surprise each other.

`expired` is computed lazily at read/validation time (a row past `expires_at` is treated as expired and its status settled on access). No background job — a sweeper is unnecessary at this volume and would be one more thing to operate.

### 3.2 Unchanged

`User`, `Group`, `Role`, `UserRole`, `GroupRole` are untouched. `auth0_user_id` / `auth0_org_id` stay as opaque string anchors (findings §3.4). **No migration of existing rows is required.**

---

## 4. Flows

### 4.1 Invite (admin invites an email)

1. `POST /v2/groups/{id}/invitations/` — unchanged request body (`{role, emails[]}`).
2. Per email: mint `token = secrets.token_urlsafe(32)`; insert `Invitation` with `sha256(token)`.
3. `EmailSender.send_invitation(...)` with `accept_url = {FRONTEND_URL}/auth/invite?invitation={token}`.
4. Response shape unchanged: `{invitations: [{email, success}]}`. A delivery failure yields `success: false` for that address and the row is rolled back, matching today's per-email partial-success semantics.

### 4.2 Accept — existing user

1. User clicks the link → frontend → `GET /v2/auth/login?invitation={token}`.
2. Look up by `sha256(token)`. Invalid/expired/accepted/revoked → redirect to the corresponding existing frontend error page (`/auth/invite/expired`, `/auth/invite/already_accepted`).
3. If the invitee email already exists in `users`: if the session is that user, redirect straight to `/v2/auth/process_invitation`; otherwise stash the token in the session and send them through OIDC login with `login_hint`.
4. `process_invitation`: assert `invitation.email == user.email`, insert `UserRole`, set `status='accepted'` and `accepted_at`, redirect to `/welcome/{group_id}`.

### 4.3 Accept — new user

Identical until step 3. The user authenticates with the OIDC provider (registering there if needed); on `/callback`, `create_user_if_not_exists` finds the pending invitation **from the session token** and uses its `group_id`.

**This removes the `org_id` claim dependency** (findings §3.1). `org_id` is an Auth0-Organizations artifact that the local OIDC mock never emits; the invitation record carries the linkage instead. Self-service signup without an invitation stays disallowed, exactly as today.

### 4.4 Login / logout

Unchanged in code. `/login`, `/callback`, `/logout` remain standard OIDC; only settings values differ per provider. `AUTH0_LOGOUT_URL` is already a config value, so Keycloak's `end_session_endpoint` drops in.

---

## 5. Frozen API contracts

These shapes **must not change** — the frontend consumes them (`frontend/src/common/queries/groups.ts`).

| Endpoint | Contract |
|---|---|
| `POST /v2/groups/` | `GroupInfoResponse` — unchanged |
| `GET /v2/groups/{id}/invitations/` | `InvitationsResponse`: `{invitations: [{id, created_at, expires_at, inviter: {name}, invitee: {email}}]}` |
| `POST /v2/groups/{id}/invitations/` | `{invitations: [{email, success}]}` |
| `GET /v2/groups/{id}/members/` | `GroupMembersResponse` — unchanged |
| `PUT /v2/users/me` | `UserMeResponse` — unchanged |
| `GET /v2/auth/login\|callback\|logout` | Redirect behavior unchanged |

The nested `inviter`/`invitee` objects are an Auth0-shaped artifact, but they are **kept verbatim** — reshaping them would force a frontend change for zero benefit. `InvitationResponse.id` is typed `str`; local integer ids are serialized as strings to preserve it.

---

## 6. Configuration

New settings, in **both** config systems (`api/settings.py` and legacy `config/config.py` — findings §3.6, where drift is a known failure mode):

| Setting | Purpose | Default |
|---|---|---|
| `IDP_JWKS_PATH` | JWKS path suffix | `/.well-known/jwks.json` (Auth0); Keycloak/IdentityServer use `/.well-known/openid-configuration/jwks` |
| `IDP_ISSUER` | Explicit issuer for JWT validation | derived from domain |
| `IDP_VERIFY_TLS` | TLS verification toggle | `true` |
| `PROVISIONING_BACKEND` | `auth0` \| `local` | `auth0` initially, `local` after Phase 2 |
| `EMAIL_BACKEND` | `ses` \| `console` | `console` |
| `EMAIL_FROM_ADDRESS` | SES sender identity | — |
| `INVITATION_EXPIRY_DAYS` | Invitation lifetime | `14` |

`IDP_JWKS_PATH` / `IDP_VERIFY_TLS` replace the hardcoded `if "genepinet.localdev" in domain` branch (`device_auth.py:47`), which the existing `TODO` on line 46 already anticipates. Behavior-driving magic hostnames become explicit configuration.

`PROVISIONING_BACKEND` is the cutover switch and the rollback lever: flipping it back to `auth0` plus a backend restart restores previous behavior without a code revert.

---

## 7. Migration & compatibility

### 7.1 Why `auth0_user_id` keeps its name

Renaming to `idp_user_id` touches ~204 code and ~153 test references for zero functional gain, and would collide with every in-flight branch. The column is already an opaque string with no format check (findings §3.4), so a Keycloak `sub` stores in it unchanged. Renaming is a cosmetic pass for after prod soaks.

### 7.2 Existing data

No backfill. `Group.auth0_org_id` values remain valid opaque keys whether they came from Auth0 or are locally minted (`grp_<uuid>`).

### 7.3 Pending Auth0 invitations at cutover

**Not migrated — re-issued.** They are short-lived and low-volume; translating Auth0 ticket formats is more risk than re-sending. The cutover runbook lists outstanding invitations before the switch so they can be re-sent after.

### 7.4 Passwords

Cannot be exported from Auth0 without an Enterprise support ticket (findings §5.2). The migration script therefore creates Keycloak accounts flagged **password-reset-required**; users set a password once on first login. Social-login users have no password and are matched by verified email.

---

## 8. Operational prerequisites (ops, not code)

Flagged explicitly because code alone does not make this work:

1. **SES sender identity verified** in the target account/region, with `ses:SendEmail` granted to the backend task role. Without this, invitations silently stop working — so `EMAIL_BACKEND=console` is the safe default and SES must be turned on deliberately.
2. **Keycloak deployed** with authorization-code **and** device grants enabled, redirect URI `{API_URL}/v2/auth/callback` (note: the live app computes `/v2/auth/callback`, not `/callback` — findings §3.6), and `sub`/`email`/`name` in the ID token.
3. **Backend restart after any secret change** — config is cached at process start. A secret change without a restart does nothing (the same trap documented for pangolin SSM updates).

---

## 9. Testing strategy

| Layer | Approach |
|---|---|
| Phase 0 | Existing suite must pass **unmodified**. That is the proof the seam changed nothing. |
| `Invitation` model | Unit tests: token hashing, expiry boundaries, status transitions, single-use enforcement |
| Provisioning | Assert on `Invitation` / `UserRole` / `Group` **rows** rather than on autospec'd `Auth0Client` calls — strictly easier, and it tests the real thing |
| Invite → accept | End-to-end through the API, using `ConsoleEmailSender` to capture the accept URL — no network |
| Request auth | Untouched; the `user_id`-header override (`conftest.py:129–163`) is provider-agnostic |
| Login/callback | Keep faking the `authlib` boundary as tests already do — provider-independent |

Tests that must be **deleted or replaced**, not repaired:

- `test_callback_syncs_auth0_user_roles` (`test_auth.py:237–264`) asserts `get_user_orgs.call_count == 1`. It tests Auth0→DB role sync, which Phase 2 removes entirely because roles are already the local source of truth (findings §2.1).

### Definition of done, per phase

- **Phase 0** — full suite green with no test edits; zero behavioral diff; provider constructed once at startup.
- **Phase 1** — JWKS/TLS behavior driven by config; local OIDC mock still authenticates in CI.
- **Phase 2** — create group → invite → accept → member holds the role, with **no Auth0 Management API call anywhere in the path**; frontend untouched.

---

## 10. Risks specific to this build

| Risk | Mitigation |
|---|---|
| **Email is new infrastructure** — a broken sender means invitations vanish silently | `console` default; per-email `success:false` surfaced in the existing response; SES enabled deliberately, not by default |
| Invitation token leakage via logs/referrer | Only the hash is stored; token travels as a query param to our own frontend; never logged by `ConsoleEmailSender` in non-dev backends |
| Two config systems drift (findings §3.6) | Every new setting added to both in the same commit |
| Provisioning was never tested against non-Auth0 (findings §2.5) | Phase 2 converts it to local DB writes, which the suite can cover directly |
| Partial invite failures leave orphan rows | Row insert and send happen per-email in one unit; failure rolls back that row |

---

*Status: specification. Implementation proceeds per [`05-implementation-plan.md`](./05-implementation-plan.md).*
