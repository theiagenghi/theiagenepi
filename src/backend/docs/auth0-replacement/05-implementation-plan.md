# Auth0 Replacement — Implementation Plan

**Date:** 2026-07-20
**Branch:** `tghi-auth0-replacement` (off `origin/tghi-dev`)
**Reads on:** [`04-spec.md`](./04-spec.md)

Ordered, testable steps. Each step names the files it touches and how it is verified. Steps are sequenced so **the suite is green after every one** — no step depends on a later step to compile or pass.

Doc 03 gives the phase strategy; this document is the build order.

---

## Step 1 — `IdentityProvider` protocol (Phase 0)

**New:** `aspen/auth/identity_provider.py`

Define a `typing.Protocol` with the seven methods in spec §2.1, reusing the existing `Auth0Org` / `Auth0User` / `Auth0OrgInvitation` TypedDicts as the data contract so autospec mocks keep working.

**Verify:** `mypy` clean; no runtime change (nothing imports it yet).

---

## Step 2 — Auth0 becomes an implementation (Phase 0)

**Touch:** `aspen/auth/auth0_management.py`

Declare `Auth0Client` as satisfying the protocol. Add thin protocol-named aliases where today's names differ (`add_org` → `create_org`, `get_org_invitations` → `list_invitations`, `delete_organization_invitation` → `delete_invitation`). **Keep the existing method names** — the test suite and `sync_auth0.py` call them directly, and renaming would be a behavioral change disguised as a refactor.

**Verify:** full suite green, unmodified.

---

## Step 3 — Build the provider once at startup (Phase 0)

**Touch:** `aspen/api/main.py`, `aspen/api/deps.py`, `aspen/api/authn.py`

Construct the provider during app startup, store on `app.state.identity_provider` (alongside `auth0_client` / `splitio`), and reduce `get_auth0_apiclient` to a lookup.

This removes a synchronous client-credentials token exchange from **every request** that touches provisioning (`authn.py:175–180`). It is a latency fix as much as a refactor.

**Care:** tests override `get_auth0_apiclient` via `dependency_overrides`; the dependency must keep its identity and signature so those overrides still bind.

**Verify:** full suite green, unmodified. Phase 0 done — provider still Auth0, behavior identical.

---

## Step 4 — Config-driven JWKS and TLS (Phase 1)

**Touch:** `aspen/auth/device_auth.py`, `aspen/api/settings.py`, `aspen/config/config.py`

Replace `if "genepinet.localdev" in domain` (`device_auth.py:47`) with explicit `IDP_JWKS_PATH` / `IDP_ISSUER` / `IDP_VERIFY_TLS` parameters, defaulted to today's Auth0 values so behavior is unchanged.

Add the settings to **both** config systems in this same commit (findings §3.6 — drift here is a known failure mode).

**Verify:** existing device-auth tests pass; local OIDC mock still authenticates (its values now come from config rather than a hostname match).

---

## Step 5 — `Invitation` model + migration (Phase 2)

**New:** `aspen/database/models/invitation.py`, one Alembic migration
**Touch:** `aspen/database/models/__init__.py`

Schema per spec §3.1. Table create only — no backfill.

**Verify:** migration applies and reverses cleanly; model unit tests (hashing, expiry boundary, single-use, status transitions).

---

## Step 6 — `EmailSender` (Phase 2e)

**New:** `aspen/util/email.py`
**Touch:** `aspen/api/settings.py`, `aspen/config/config.py`

`EmailSender` interface + `SESEmailSender` + `ConsoleEmailSender`; selected by `EMAIL_BACKEND`, defaulting to `console`.

**Why before provisioning:** invitations cannot be built without a delivery mechanism, and defaulting to `console` keeps every test hermetic — no network, no AWS.

**Verify:** unit tests for both backends; console backend captures the accept URL for use by end-to-end tests.

---

## Step 7 — `LocalProvisioning` (Phase 2)

**New:** `aspen/auth/local_provisioning.py`

Implements the Step 1 protocol against `Group` / `UserRole` / `Invitation` per spec §2.2. Selected by `PROVISIONING_BACKEND`.

**Verify:** unit tests asserting on rows, not on mocked API calls.

---

## Step 8 — Rewrite the call sites (Phase 2)

**Touch:** `aspen/api/views/groups.py`, `aspen/api/views/users.py`, `aspen/api/views/auth.py`

- `create_group`: mint `auth0_org_id` locally instead of `add_org`.
- `get_group_invitations` / `invite_group_members`: go through the provider; preserve the frozen response shapes (spec §5).
- `get_invitation_ticket`: the list-and-scan loop (`auth.py:30–48`) collapses to one indexed `token_hash` lookup.
- `create_user_if_not_exists`: drop the `org_id`-claim requirement; take `group_id` from the invitation.
- `process_invitation`: validate the local invitation; insert `UserRole`; mark accepted.
- Remove the `sync_auth0_roles` Split gate and `RoleManager` sync (already dead on `/callback`; roles are local).

**Verify:** end-to-end create group → invite → accept → role present, with no Management API call in the path.

---

## Step 9 — Test updates (Phase 2)

**Touch:** `aspen/api/views/tests/test_invitation_flows.py`, `test_groups.py`, `test_auth.py`

Convert assertions from mocked `Auth0Client` calls to `Invitation` / `UserRole` rows. **Delete** `test_callback_syncs_auth0_user_roles` (`test_auth.py:237–264`) — it asserts on role-sync that Phase 2 removes (spec §9).

**Verify:** full suite green.

---

## Step 10 — Retire dead Auth0 code (Phase 2 tail)

**Delete:** `aspen/auth/role_manager.py`, `aspen/cli/sync_auth0.py`

Only once nothing imports them. `auth0_management.py` **stays** — it remains the `auth0` provisioning backend and the rollback path until prod cutover completes (spec §6).

**Verify:** `rg` shows no live imports; suite green.

---

## Step 11 — User-migration script (Phase 3′)

**New:** `aspen/cli/migrate_users_to_idp.py`

Reads `users`, creates corresponding IdP accounts flagged password-reset-required, maps `sub` → existing `auth0_user_id`, and supports `--dry-run`. **Not executed here** — ops runs it against a staging clone first (spec §7.4).

**Verify:** `--dry-run` against local dev data produces a correct plan and writes nothing.

---

## Sequencing and rollback

Steps 1–3 (Phase 0) and 4 (Phase 1) are behavior-neutral and independently mergeable. Steps 5–10 land the functional change, gated behind `PROVISIONING_BACKEND`; until it is flipped to `local`, the running system behaves exactly as today.

Rollback at any point before decommissioning Auth0 is `PROVISIONING_BACKEND=auth0` plus a backend restart — a config flip, not a code revert.

## Verification gate

Nothing here is "done" on inspection. The gate is the backend suite (`make backend-tests`) green, plus the Phase-2 end-to-end assertion that a full invite→accept cycle touches no Auth0 API. Test results are reported as run, including failures.

---

## Corrections made during implementation

The plan above was written before the code. Four things in it turned out to be
wrong, and the implementation deviates deliberately. Recorded here so the
document does not quietly disagree with the branch.

### 1. The provider protocol is async, not sync (Steps 1, 3, 8)

The plan specified a synchronous protocol mirroring `Auth0Client`'s method
shapes. That is unimplementable for the local backend: it reads and writes
through an async SQLAlchemy session, and there is no way to await inside a sync
method from within a running event loop.

`IdentityProvider` is therefore async, and `Auth0Provisioning` wraps the
synchronous client in `starlette.concurrency.run_in_threadpool`.

**Consequence:** Phase 0 is *not* behavior-neutral, as the plan claimed. It is a
behavior improvement — the previous code performed blocking Auth0 HTTP calls
directly inside async request handlers, stalling the whole event loop for the
duration of each call. That was a latent bug, and the async seam fixes it.

### 2. No alias renaming (Step 2)

The plan called for renaming `Auth0Client` call sites to a neutral alias so the
protocol could be introduced. Unnecessary: `typing.Protocol` uses *structural*
subtyping, so `Auth0Provisioning` conforms without inheriting from or
registering with anything. Step 2 was dropped entirely.

### 3. The provider is built lazily, not at startup (Step 3)

Constructing `Auth0Client` performs a client-credentials token exchange. Local
dev and CI run with placeholder management credentials, so building it at
startup would fail before the app could serve anything. It is instead cached on
`app.state` on first use. There is no `await` between the read and the write, so
a single event loop cannot interleave a duplicate construction.

The local backend is built per request rather than cached, because it holds the
request's database session.

### 4. `role_manager.py` is trimmed, not deleted (Step 9)

Only `RoleManager.sync_user_roles` was Auth0-coupled. `get_role_by_name`,
`generate_user_role` and `generate_group_role` are pure database helpers used by
the test fixtures in `aspen/test_infra/models/usergroup.py`. Deleting the module
would have broken the suite.

### 5. Role granting moved out of the provider (Step 8)

`accept_invitation` originally would have needed to write local `user_roles`
rows, which would have forced a database session into the Auth0 adapter and
leaked application concerns across the IdP boundary.

Instead the provider only *consumes* the invitation (Auth0: add org member, then
delete the ticket; local: mark `ACCEPTED`), and the view calls
`grant_invitation_roles()` — one shared code path for every backend, since
authorization has always lived in our own database.

This replaces the old post-hoc `sync_user_roles` round-trip. Roles now come from
the invitation itself, which also removes a way for the two to disagree:
`test_process_invitation_success` previously asserted the user ended up with
`admin` because the mock stubbed `get_org_user_roles → ["admin"]` while the
invitation granted `["member"]`. Real Auth0 would have reported `member`. The
assertion now reads `member`.

### 6. Two defects found while reviewing the local backend

Both were introduced by this branch, in `LocalProvisioning`, and both are
covered by tests now.

**Cross-group invitation replay.** `get_invitation(org_id, token)` looked the
token up by hash alone and ignored `org_id`. Because `/process_invitation` takes
`organization` from the query string and `grant_invitation_roles` then grants the
roles *in whatever group that id names*, a legitimate invitee could replay their
own valid token with a different `organization` and gain roles in a group they
were never invited to. The lookup is now joined to the group the invitation was
issued for. The Auth0 path was never exposed to this — Auth0 scopes the lookup
server-side — which is exactly why it was easy to miss when writing the local
twin against a passing Auth0 test suite. Regression test:
`test_invitation_cannot_be_redeemed_against_another_group`.

**Inviter resolved by display name.** `invite_member` did
`SELECT user WHERE name = <inviter name>` and called `.one()`. Display names are
not unique, so two users sharing a name would raise `MultipleResultsFound` (a
500), and a near-miss would have attributed the invitation to the wrong person.
The protocol now takes the inviter as `(inviter_id, inviter_name)`: the local
backend keys off the id, and Auth0 keeps using the name because that is what it
renders in the email it sends. Resolving one from the other is not safe in
either direction, so both are passed explicitly.
