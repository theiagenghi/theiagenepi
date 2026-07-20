# Replacing Auth0 in theiagenepi — Analysis, Options, and Plan

**Date:** 2026-07-13
**Question asked:** Can we swap Auth0 for a built-in or another free identity manager?
**Short answer:** **Yes — and less of it needs replacing than it appears.** Authorization is already local, the frontend has zero Auth0 coupling, and the login flow already runs against a non-Auth0 OIDC server in dev/CI. The real work is provisioning + a new local invitations store.

---

## The three documents

1. **[01-current-state-findings.md](./01-current-state-findings.md)** — Exactly what Auth0 does today, what is already decoupled, and what remains bound to it. Every claim is code-referenced.
2. **[02-options-and-recommendation.md](./02-options-and-recommendation.md)** — The full option space (self-hosted IdP vs. built-in), a comparison matrix, and the recommendation with honest trade-offs.
3. **[03-implementation-and-migration-plan.md](./03-implementation-and-migration-plan.md)** — A phased, reversible plan: files, schema, endpoints, user migration (incl. the password-hash constraint), rollback, testing, and risks.

---

## Recommendation at a glance

> **Run a self-hosted OIDC provider (Keycloak) for authentication only, and move organizations, roles, and invitations into the application's PostgreSQL.**

This finishes a migration the codebase is already ~70% through, keeps the security-sensitive primitives in a battle-tested IdP, and removes lock-in instead of relocating it to a new vendor.

## Why the scope is smaller than "rip out Auth0"

| Auth0's job here | Status |
|---|---|
| Authorization (who-can-do-what) | ✅ **Already local** — PostgreSQL role tables + Oso; role-sync disabled |
| Frontend integration | ✅ **Zero coupling** — no Auth0 SDK; just redirects to backend routes |
| Login handshake (OIDC) | 🟡 **Portable** — already proven against a self-hosted OIDC server in dev/CI |
| CLI auth (device flow + JWKS) | 🟡 **Portable** — already has a working non-Auth0 `local` config |
| Terraform / infra | ✅ **Auth0-agnostic** — secret is an opaque blob; no plumbing changes |
| Provisioning (orgs/members/roles) | 🔴 **The work** — bespoke Auth0 Management API integration |
| Invitations | 🔴 **The work** — exist *only* in Auth0; no local store yet |

The center of gravity is **invitations + provisioning**. Everything else is already portable or already done.

## Key constraints

- **Cost driver:** this app uses Auth0 **Organizations** (a B2B feature) — B2B plans start ~$150–800/mo, not the hobby free tier.
- **Migration constraint:** Auth0 will not export password hashes without an Enterprise support ticket (a week+). Plan assumes **password-reset-on-first-login** (or lazy migration), never a silent hash copy. Identity records (email/name/roles/membership) are already in our DB and migrate for free.

---

*Status: analysis + recommendation + plan. No implementation code has been written. Awaiting review before any build begins.*
