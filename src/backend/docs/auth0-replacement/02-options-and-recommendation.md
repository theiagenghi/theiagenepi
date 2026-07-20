# Auth0 Replacement — Options and Recommendation

**Date:** 2026-07-13
**Reads on:** [`01-current-state-findings.md`](./01-current-state-findings.md)
**Feeds:** [`03-implementation-and-migration-plan.md`](./03-implementation-and-migration-plan.md)

This document maps the full option space for replacing Auth0 with a free / self-hostable alternative, weighs each against what the codebase *actually* needs (per the findings), and gives a single recommendation with honest trade-offs.

---

## 1. What the replacement must actually provide

From the findings, the requirements are narrower than "everything Auth0 does":

**Must have (hard requirements):**
1. **OIDC authorization-code login** with discovery (`/.well-known/openid-configuration`), `/authorize`, `/token`, and a `userinfo`/ID-token carrying `sub`, `email`, `name`. — *Already proven against a non-Auth0 provider locally.*
2. **OAuth 2.0 Device Authorization Grant (RFC 8628)** + a **JWKS** endpoint for RS256 verification. — *Required by the CLI; not all lightweight providers implement device flow.*
3. A way to **provision and look up** orgs, memberships, roles, and **invitations** — *either* via the provider's admin API *or* by moving this into our own database.
4. **Self-hostable and free** (the stated goal): no per-MAU billing.

**Explicitly NOT required (already handled locally):**
- Request-time authorization / RBAC evaluation — done in-app via `Role`/`UserRole`/`GroupRole` + Oso.
- An org/tenant *model* in the IdP — we already have `Group`. The IdP does not need a native "organizations" feature **if** we keep org/role/invitation data in our DB (see Path B).
- Anything on the frontend — zero coupling.

**The pivotal design fork** is requirement #3: **does the identity provider own orgs/roles/invitations, or do we?**

- **Path A — provider owns them.** Pick an IdP with a first-class organizations + invitations API (Keycloak, Zitadel, Logto) and rewrite `Auth0Client` as an adapter over *its* admin API. Keeps today's architecture shape; keeps a heavy external dependency.
- **Path B — we own them.** Use the IdP for **login only**; move orgs/roles/invitations fully into our PostgreSQL. The IdP needs only `sub`/`email`/`name` + device flow, so *any* compliant OIDC provider works and most of `Auth0Client` is **deleted**, not ported.

This fork matters more than the specific product choice, so it is treated first.

---

## 2. The three solution tiers

### Tier A — Self-hosted OIDC drop-in that also owns orgs/roles/invitations (Path A)

Run a full-featured IdP and re-point everything at it, including provisioning. Rewrite `Auth0Client` to call the IdP's admin API.

**Candidates** (all self-hostable, free core):

| Product | Lang / footprint | Orgs + invitations | Device flow | License | Stack fit |
|---|---|---|---|---|---|
| **Keycloak** | Java/Quarkus; heaviest | **Organizations GA in v26**, incl. an **org-invitations REST API** (`/admin/realms/{realm}/orgs/{orgId}/invitations`) — closest 1:1 map to Auth0 | Yes | Apache-2.0 | Ops-heavy but ubiquitous |
| **Zitadel** | Go; single binary | Orgs are **first-class** (best multi-tenant DX); native invitations | Yes | **AGPL-3.0** (relicensed 2025) | Clean API, but copyleft |
| **Logto** | Node; modern | Native orgs + invitations | Yes | MPL-2.0 | Good DX, newer/smaller community |
| **Authentik** | **Python/Django** (team stack) | Multi-tenancy is **not a core strength** (brands/tenants ≠ Auth0 orgs); RBAC differs | Yes | MIT + enterprise | Best language fit, weakest org model |

**Pros:** preserves the current architecture (IdP is the system of record for orgs/invitations); the org-invitations REST APIs of Keycloak/Zitadel/Logto map closely onto our existing call sites; you inherit MFA, social login, password reset, and an admin console for free.

**Cons:** you still run and integrate a heavyweight external service; you rewrite `Auth0Client` against a *new* proprietary admin API (real work, and you're re-coupling to another vendor's org model); invitations remain external to your DB, so the awkward "look up a ticket via list-all-invitations" pattern likely persists; **you have not reduced architectural lock-in — only moved it.**

**License note:** for a redistributable fork, **Zitadel's AGPL-3.0** is a genuine consideration (network-copyleft). **Keycloak (Apache-2.0)**, **Authentik (MIT)**, and **Logto (MPL-2.0)** are friendlier.

### Tier B — Self-hosted OIDC for login only; orgs/roles/invitations move into our DB (Path B) — **RECOMMENDED**

Use a self-hosted IdP purely as the **authenticator**. It emits `sub`/`email`/`name` and supports device flow + JWKS — nothing more. Everything org/role/invitation-shaped moves into PostgreSQL, where **two of the three already live** (`Group` = org, `Role`/`UserRole`/`GroupRole` = roles). The only net-new piece is an **`Invitation` table**.

`Auth0Client` is **mostly deleted**: no more `add_org`, `add_org_member`, `invite_member`, `get_org_invitations`, `delete_organization_invitation`, role-sync. Group creation writes a `Group` row (as it does now, minus the `add_org` call). Invitations become local rows with a signed token and expiry. `create_user_if_not_exists` stops needing an `org_id` claim — the invitation record carries `group_id`.

**Pros:**
- **Finishes a migration that's already ~70% done.** Authorization is local; orgs already mirrored in `Group`; role-sync already disabled. This path completes the trajectory the code is already on.
- **Minimizes lock-in.** The IdP becomes swappable — Keycloak today, anything OIDC tomorrow, or even Tier C's built-in login later — without touching org/role/invitation logic.
- **You own only non-cryptographic domain logic** (invitations, membership). The security-sensitive primitives (password storage, reset, MFA, device-grant, JWKS) stay in a battle-tested IdP.
- Removes the awkward Auth0 invitation-ticket lookup pattern; invitations become first-class, queryable rows with real foreign keys.
- **Provider choice becomes low-stakes** (login-only), so you can pick on ops comfort, not org-model fit.

**Cons:**
- Net-new **`Invitation` model + endpoints + email + token/expiry semantics** (the one real build).
- You still run one self-hosted IdP (ops surface, though a minimal one).
- Group-create and invite flows are rewritten (bounded, well-understood work).

**Concrete provider for Tier B:** **Keycloak** (Apache-2.0, mature, device flow, gives MFA/reset/social for free) — but used **only for login**, so its org features are irrelevant and we are *not* locked to its model. Zitadel/Logto/Authentik are all fine substitutes here precisely because the role is minimal.

### Tier C — Fully built-in app auth, no external IdP at all

Add email+password to the app: password hash column (argon2), server-issued sessions (the signed-cookie middleware already exists), our own password-reset and email-verification, and — the sticking point — our **own token issuance for the CLI**.

**Pros:** zero external auth dependency; simplest deployment (no extra service); total control.

**Cons — and why it's not the recommendation:**
- The CLI uses the **Device Authorization Grant + JWKS**. Going fully built-in means **implementing an RFC 8628 device-authorization server and a JWKS/JWT issuer ourselves** — security-sensitive protocol code that IdPs give us for free. This single requirement makes "pure built-in" materially more expensive and riskier than Tier B.
- We would own **all** credential security: password hashing, reset-token generation/expiry, rate limiting, lockout, optional MFA. Easy to get subtly wrong.
- No free MFA / social login / account console.

Tier C only becomes attractive if the CLI/device-flow requirement is dropped. Given it exists and works, Tier B gets the same "no per-MAU billing, self-owned" benefits **without** hand-rolling auth crypto.

---

## 3. Comparison matrix

Scored for *this* codebase (✓✓ strong, ✓ ok, ✗ weak):

| Criterion | Tier A (provider owns orgs) | **Tier B (login-only + local orgs)** | Tier C (fully built-in) |
|---|---|---|---|
| Reuses "authz already local" | ✓ | **✓✓** | ✓✓ |
| Reuses "frontend zero-coupling" | ✓✓ | **✓✓** | ✓✓ |
| Reuses proven local OIDC flow | ✓✓ | **✓✓** | ✗ (drops OIDC) |
| CLI device flow for free | ✓✓ | **✓✓** | ✗ (build it) |
| Net-new code | Medium (adapter rewrite) | **Medium (invitations)** | High (auth crypto + device server) |
| Ongoing lock-in | ✗ (new vendor org model) | **✓✓ (swappable)** | ✓✓ (none) |
| Security-sensitive code we own | ✓✓ (little) | **✓✓ (little)** | ✗ (a lot) |
| Ops burden | ✗ (heavy IdP + its org model) | **✓ (minimal IdP)** | ✓✓ (no IdP) |
| Migration of real users | ✓ | **✓** | ✓ |
| Free / self-hosted | ✓✓ | **✓✓** | ✓✓ |

---

## 4. Recommendation

> **Adopt Tier B: run a self-hosted OIDC provider (Keycloak) for authentication only, and move organizations, roles, and invitations fully into the application's PostgreSQL database.**

### Why

1. **It finishes what the codebase already started.** Authorization is local, orgs are mirrored in `Group`, role-sync is disabled. Tier B completes that direction instead of re-coupling to a new vendor's org model (Tier A) or discarding the working OIDC/device-flow machinery (Tier C).
2. **It draws the security boundary in the right place.** The hard, dangerous primitives (password storage, reset, MFA, device grant, JWKS) stay in a mature IdP. We own only invitations and membership — ordinary CRUD with foreign keys, not crypto.
3. **It removes lock-in rather than relocating it.** Because the IdP is login-only, it is swappable. That is the opposite of trading Auth0's lock-in for Keycloak's.
4. **The one real build — a local `Invitation` table — is bounded and desirable anyway.** It replaces today's fragile "list every Auth0 invitation to find one ticket" pattern with real, queryable rows. It also lets `create_user_if_not_exists` drop its dependency on the non-standard `org_id` claim.
5. **The login path is already proven** against a non-Auth0 OIDC server in dev and CI. Keycloak steps into the exact slot `oidc-server-mock` occupies today.

### Why Keycloak specifically (a low-stakes pick)

Apache-2.0 (fork-friendly), the largest community, first-class device flow, and free MFA / password-reset / social login / admin console. Its heavyweight *organizations* features are simply **unused** in Tier B, so its main downside doesn't apply. Because the role is login-only, **Zitadel, Logto, or Authentik are drop-in substitutes** — choose on ops preference. (If the team wants a *lighter* footprint than Keycloak and can accept a smaller community, **Zitadel**'s single Go binary is the leading alternative; note its **AGPL-3.0** license for a redistributable fork.)

### Honest trade-offs we accept

- We run one self-hosted IdP (patching, availability, backups). Mitigated by it being login-only and well-trodden.
- We build and maintain an invitations subsystem (email, tokens, expiry). This is genuinely new code — but small, non-cryptographic, and an architectural improvement.
- Group-create and invitation flows are rewritten. Bounded; covered step-by-step in doc 03.
- The **provisioning path has never run against a non-Auth0 provider** (the local mock has no Management API), so Tier B's provisioning work needs fresh test coverage — which, since it becomes local DB writes, is *easier* to test than an external API.

### What we explicitly do NOT do

- No frontend changes (zero coupling).
- No Terraform/plumbing changes (secret is an opaque blob; only its *contents* change).
- No schema change to the identity anchors initially — reuse `auth0_user_id`/`auth0_org_id` as opaque columns; an optional rename to `idp_*` is a later cosmetic pass.
- No authorization/RBAC changes — already local.

---

## 5. Sources (external facts, verified 2026-07-13)

- Auth0 pricing / B2B Organizations tiers — [auth0.com/pricing](https://auth0.com/pricing)
- Keycloak Organizations GA + org-invitations REST API — [Keycloak 26 Organizations](https://www.keycloak.org/2024/06/announcement-keycloak-organizations), [release notes](https://www.keycloak.org/docs/latest/release_notes/index.html)
- Auth0 password-hash export limitation — [Auth0 user-migration docs](https://auth0.com/docs/manage-users/user-migration)
- Self-hosted IdP comparison (orgs, license, footprint) — [open-source auth comparison 2026](https://skycloak.io/blog/open-source-authentication-comparison-2026/), [Authentik vs Zitadel](https://wz-it.com/en/blog/authentik-vs-zitadel-identity-provider-comparison/)
