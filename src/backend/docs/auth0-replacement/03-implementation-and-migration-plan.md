# Auth0 Replacement — Implementation & Migration Plan

**Date:** 2026-07-13
**Reads on:** [`01-current-state-findings.md`](./01-current-state-findings.md), [`02-options-and-recommendation.md`](./02-options-and-recommendation.md)
**Recommended option:** Tier B — self-hosted **Keycloak for login only**; **orgs/roles/invitations owned by our PostgreSQL**.

This is a phased plan, not a merge-ready patch. It names the files to touch, the schema to add, the endpoints to change, the data-migration mechanics (including the password-hash constraint), rollback, testing, and risks. It is intentionally incremental: each phase is shippable and reversible on its own.

---

## Guiding principles

1. **Introduce a seam before swapping the implementation.** Put an `IdentityProvider` interface between the app and Auth0 *first*, with Auth0 as the initial implementation. Then swap the implementation behind it. This keeps every phase green.
2. **Keep the DB anchors opaque.** Reuse `auth0_user_id` / `auth0_org_id` as generic string columns during the migration. Rename to `idp_*` only at the very end, if at all.
3. **Prove login before touching provisioning.** Login is already proven against a non-Auth0 OIDC server; provisioning is not. Sequence accordingly.
4. **Dual-run, don't big-bang.** Stand up Keycloak alongside Auth0 and cut over one environment at a time (rdev → staging → prod).

---

## Phase 0 — Abstraction seam (no behavior change)

**Goal:** make Auth0 pluggable without changing what the app does. Everything stays green; nothing deploys differently.

- Define an `IdentityProvider` protocol capturing the two real responsibilities:
  - **AuthN config**: the OIDC URLs + client credentials the login/callback/CLI paths need (already all funnel through `settings.py`).
  - **Provisioning**: the methods actually called today — `create_org`, `add_member`, `remove_member`, `assign_roles`, `list_invitations`, `create_invitation`, `delete_invitation`, `update_user`. Mirror the existing `Auth0Org` / `Auth0User` / `Auth0OrgInvitation` TypedDicts (`auth0_management.py:10–51`) as the interface's data contract so the test mocks keep working unchanged.
- Make the current `Auth0Client` the first implementation of that protocol (thin rename/adapter; no logic change).
- Route `get_auth0_apiclient` (`authn.py:175–180`) through a factory that returns the configured provider. Fix the per-request-construction smell here: build the client **once at startup** and stash it on `app.state` (like `auth0_client`/`splitio`), instead of re-doing a client-credentials token exchange on every request.

**Files:** `aspen/auth/` (new `identity_provider.py` interface; `auth0_management.py` becomes an implementation), `aspen/api/authn.py`, `aspen/api/deps.py`, `aspen/api/main.py`.
**Exit test:** full backend suite green; no functional diff. Provider is still Auth0.

---

## Phase 1 — Stand up Keycloak as an OIDC login provider

**Goal:** authenticate against Keycloak in a real (non-local-mock) environment, login path only. Provisioning still on Auth0.

- Deploy Keycloak (start in **rdev**). It occupies the same slot `oidc-server-mock` holds locally — the login/callback/device-flow code needs **no change**, only config.
- Populate the `genepi-config` secret for that environment with Keycloak's OIDC values:
  - `AUTH0_DOMAIN` → Keycloak realm host
  - `AUTH0_BASE_URL`, `AUTH0_AUTHORIZE_URL`, `AUTH0_ACCESS_TOKEN_URL`, `AUTH0_SERVER_METADATA_URL` → Keycloak realm endpoints (via the `SECRET_AUTH0_*` overrides in `settings.py:118–121`)
  - `AUTH0_CLIENT_ID` / `AUTH0_CLIENT_SECRET` → the Keycloak client
  - `AUTH0_CLIENT_KWARGS` → `{"scope": "openid profile email"}`
- Configure the Keycloak realm/client: authorization-code + **device** grants, redirect URIs `{API_URL}/v2/auth/callback` (note the live app always computes `/v2/auth/callback`, *not* `/callback` — align Keycloak to that), and claims so the ID token carries `sub`, `email`, `name`.
- Generalize the one hardcoded branch in `device_auth.py:47` (`if "genepinet.localdev" in domain`). Replace the domain-substring check with an explicit config flag (e.g. `IDP_JWKS_PATH` + `IDP_VERIFY_TLS`) so JWKS-path/TLS behavior is driven by settings, not a magic hostname. (The TODO on line 46 already anticipates this.)
- Mirror the config in **both** config systems (`settings.py` *and* legacy `config/config.py`) so CLI/migrations/workflows agree with the live app (findings §3.6).

**Terraform:** none for the secret payload (opaque blob). Only the new Keycloak *service* itself needs infra.
**Exit test:** in rdev, browser login and `aspencli --env rdev` device login both succeed against Keycloak; `/v2/users/me` returns the user. Provisioning still calls Auth0.

---

## Phase 2 — Move orgs, roles, and invitations into PostgreSQL

**Goal:** replace the Auth0 Management API with local logic. This is the core of the migration and the only substantial build.

### 2a. New `Invitation` model

Add an invitations table (the store that does not exist today, findings §3.5):

```
Invitation
  id             PK
  group_id       FK -> groups.id            (the org being joined)
  email          str                        (invitee)
  roles          str[] / assoc              (e.g. ["member"])
  token_hash     str, unique                (hash of the emailed token; never store raw)
  invited_by     FK -> users.id
  created_at     datetime
  expires_at     datetime
  accepted_at    datetime, nullable
  status         enum(pending, accepted, expired, revoked)
```

Alembic migration adds the table only (no backfill needed — pending invitations are transient; see §Migration).

### 2b. Rewrite provisioning against local tables

Behind the Phase-0 `IdentityProvider` seam, add a `LocalProvisioning` implementation:

| Today (Auth0) | Becomes (local) |
|---|---|
| `add_org` on `POST /groups/` | Just create the `Group` row (drop the Auth0 call). Generate `auth0_org_id` locally (e.g. `grp_<uuid>`) to keep the NOT-NULL column satisfied. |
| `invite_member` on `POST /groups/{id}/invitations/` | Insert an `Invitation` row; generate a signed token; send email with `{FRONTEND_URL}/auth/invite?token=…`. |
| `get_org_invitations` (list) | `SELECT ... FROM invitations WHERE group_id=…` |
| `get_invitation_ticket` (lookup by ticket) | Look up by `token_hash` (`views/auth.py:30–48` shrinks to one indexed query). |
| `add_org_member` + `delete_organization_invitation` in `/process_invitation` | Insert `UserRole` rows from the invitation's `roles`; mark invitation `accepted`. |
| `RoleManager.sync_user_roles` (Auth0→DB) | **Delete.** Roles are already the local source of truth. |
| `update_user` (name) on `PUT /users/me` | Update the local `User.name` only (drop the Auth0 call). |

### 2c. Simplify the login/invitation flow

- `create_user_if_not_exists` (`views/auth.py:149–184`): **drop the `org_id`-claim requirement.** New users are created from an accepted invitation whose `group_id` supplies the linkage. Self-service (non-invited) signup remains disallowed exactly as today.
- `/process_invitation` (`views/auth.py:252–317`): validate the local `Invitation` (email match, not expired, not accepted) instead of an Auth0 ticket; write `UserRole`s; mark accepted.
- Remove the Split.io `sync_auth0_roles` gate and the `RoleManager` sync entirely (already dead on the `/callback` path).

### 2d. Test coverage (this surface was never tested against a non-Auth0 provider)

- Because provisioning is now **local DB writes**, it is far easier to test than an external API. Convert `test_invitation_flows.py` / `test_groups.py` from asserting on autospec'd `Auth0Client` calls to asserting on `Invitation` / `UserRole` rows.
- Delete/replace `test_callback_syncs_auth0_user_roles` (`test_auth.py:237–264`) — it asserts `get_user_orgs.call_count == 1`, which is already broken by the commented-out sync (findings §2.1) and becomes irrelevant.

**Files:** `aspen/database/models/usergroup.py` (+ new `invitation.py`), a new Alembic migration, `aspen/auth/` (local provisioning impl), `aspen/api/views/auth.py`, `views/groups.py`, `views/users.py`, `aspen/auth/role_manager.py` (delete), `aspen/cli/sync_auth0.py` (delete/retire), and the corresponding tests.
**Exit test:** create group → invite → accept → member has role, all against local tables, no Auth0 Management API calls anywhere. rdev green end-to-end.

---

## Phase 3 — Migrate real users and orgs

**Goal:** every existing production user and org keeps working after cutover. This is where the **password-hash constraint** (findings §5.2) governs the approach.

### What migrates trivially (already ours)

Email, name, org membership, roles, and the `Group`/`User` rows are **already in our PostgreSQL** — nothing to export. `auth0_user_id` / `auth0_org_id` stay as the join keys.

### What cannot be copied: passwords

Auth0 will not hand over password hashes without an Enterprise support ticket that can take a week or more. Two viable strategies:

- **Option A — Password reset on first login (recommended, simplest).**
  1. Bulk-create a Keycloak user for each existing `User`, keyed so the Keycloak `sub` maps to our `auth0_user_id`. The cleanest mapping is to **set the Keycloak user's `sub` (or a stable external-id claim) equal to the existing `auth0_user_id`** so no DB rewrite is needed. If that is impractical, add a one-time `idp_user_id` column and backfill it, keeping `auth0_user_id` for historical joins.
  2. Mark each Keycloak account "requires password reset" / send a set-password email.
  3. Users click through Keycloak's reset flow once; thereafter login is normal.
  - **Pro:** no hashes needed; no waiting on Auth0; users re-establish credentials securely. **Con:** every user must reset once (communicate ahead of cutover).

- **Option B — Lazy / trickle migration.** Keep Auth0 as a fallback verifier: on first post-cutover login, authenticate against Auth0 once, capture the just-verified password, write it into Keycloak, then never use Auth0 again for that user. **Pro:** no user-visible reset. **Con:** more moving parts; requires Auth0 to stay live during the drain window; still needs careful handling. Prefer only if a forced reset is unacceptable to stakeholders.

> **Social logins** (Google, etc.) have no password at all — for those users, configure the same identity provider in Keycloak and match on verified email; no reset applies.

### Org/invitation migration

- Orgs: no export — `Group` rows already exist. If Path-A features were ever relied on, none are (findings), so nothing to carry.
- Pending invitations: transient. Rather than migrate Auth0 tickets, **re-issue** any outstanding invitations from the new local system at cutover (small volume, cleaner than translating ticket formats).

**Exit test:** a migrated production-shaped user (from a staging clone) logs in via Keycloak, lands on their groups with correct roles.

---

## Phase 4 — Cutover, per environment

Cut over **rdev → staging → prod**, one at a time, dual-running Keycloak and Auth0 until each environment is verified.

Per environment:
1. Point that environment's `genepi-config` secret at Keycloak (Phase 1 keys) and enable local provisioning (Phase 2).
2. **Restart the backend** — config is cached at process start (`get_app()` runs at import), the same reason `make local-init` restarts the backend after writing the secret, and the same caching behavior documented for pangolin SSM updates. A secret change without a restart does nothing.
3. Run the smoke suite: browser login, CLI device login, invite→accept, `/v2/users/me`, a role-gated action.
4. Keep Auth0 credentials in the secret (unused) until the environment is confirmed, to enable instant rollback.

**Terraform:** unchanged for the secret payload. Standard deploy mechanics otherwise.

---

## Phase 5 — Decommission and clean up

Only after prod is stable for an agreed soak period:

- Remove `auth0-python` from `pyproject.toml`; delete `auth0_management.py`, `role_manager.py`, `sync_auth0.py`, `scripts/auth0_login.sh`.
- Replace the `auth0.v3` JWT helper in `device_auth.py` with a maintained JOSE library (e.g. `python-jose`/`authlib` JWKS) so the last `auth0.*` import is gone.
- Retire the now-unused Auth0 keys from `genepi-config` in every environment.
- **Optional cosmetic pass:** rename `auth0_user_id` → `idp_user_id`, `auth0_org_id` → `idp_org_id` across models, ~204 code references, and ~153 test references. Purely readability; defer unless the team wants it.
- Repair the stale CLI tests (findings §2.3) so the device-auth flow has real coverage going forward.
- Point the local `oidc-server-mock` config and CI at the same claim shapes Keycloak uses, so dev/CI keep mirroring prod.

---

## Rollback

- **Within a phase:** every phase is behind the Phase-0 seam or a config value. Revert the secret to Auth0 values + redeploy (restart) → back on Auth0. Because Auth0 credentials are retained through Phase 4, rollback is a config flip, not a code revert.
- **After Phase 5 cleanup:** rollback requires restoring deleted code, so do not start Phase 5 until prod has soaked. Keep the pre-cleanup commit tagged.

---

## Testing strategy

| Layer | Approach |
|---|---|
| Unit (provisioning) | Now local DB writes — assert on `Invitation`/`UserRole`/`Group` rows instead of mocked `Auth0Client` calls. Strictly easier than before. |
| Request auth | Unchanged — the `user_id`-header override seam (`conftest.py:129–163`) is provider-agnostic and keeps working. |
| Login/callback | Keep faking the `authlib` boundary (`authorize_access_token.side_effect`), as tests already do — provider-independent. |
| CLI | **Repair the stale suite first** (findings §2.3), then add device-flow coverage against a Keycloak test realm. |
| End-to-end | Reuse the local `oidc-server-mock` for CI; add a Keycloak-backed smoke test in rdev. |

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Provisioning path never tested vs non-Auth0 (findings §2.5) | High | Med | Phase 2 makes it local + well-tested; rdev-first |
| Forced password reset annoys users | High | Med | Communicate ahead; or use lazy migration (Phase 3 Option B) |
| Two config systems drift (findings §3.6) | Med | Med | Update `settings.py` **and** `config/config.py` together; add a check |
| Keycloak ops burden (patching/availability) | Med | Med | Login-only footprint; managed/HA deploy; it's well-trodden |
| Backend not restarted after secret change | Med | High | Bake restart into the cutover runbook (Phase 4.2) |
| `org_id`-claim assumptions linger | Low | Low | Phase 2c removes the dependency explicitly |
| Stale CLI tests hide a device-flow regression | Med | Med | Repair in Phase 5 (or earlier if CLI is touched) |
| Social-login users have no password to reset | Low | Low | Configure same IdP in Keycloak; match on verified email |

---

## Effort shape (relative, not calendar estimates)

| Phase | Size | Risk |
|---|---|---|
| 0 — Abstraction seam | S | Low |
| 1 — Keycloak login (rdev) | S–M | Low (login already proven) |
| 2 — Local orgs/roles/invitations | **L** | **Med (the real build)** |
| 3 — User/password migration | M | Med (password-reset comms) |
| 4 — Cutover per env | M | Med (ops discipline) |
| 5 — Cleanup + optional rename | S–M | Low |

The weight is squarely in **Phase 2**. Everything else is configuration, sequencing, and communication.
