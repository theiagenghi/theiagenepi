"""Provisioning interface shared by every identity backend.

Authentication is deliberately absent: login is plain OIDC, handled by authlib
(web) and `device_auth.py` (CLI), neither of which needs a provider object. This
protocol covers provisioning only -- organizations, membership and invitations.

The methods are async because the local backend reads and writes through an
async SQLAlchemy session. The Auth0 adapter is a synchronous HTTP client, so it
runs its calls in a threadpool rather than blocking the event loop -- which is
what the previous in-line calls were quietly doing.

The shapes here follow what the application needs, not Auth0's HTTP API. Each
backend translates.
"""

from typing import List, Optional, Protocol, TypedDict


class InvitationInfo(TypedDict):
    """One outstanding invitation, in the shape the API layer serialises."""

    id: str
    created_at: str
    expires_at: str
    inviter_name: str
    invitee_email: str
    roles: List[str]


class IdentityProvider(Protocol):
    async def create_org(self, group_prefix: str, group_name: str) -> str:
        """Create an organization and return its identifier."""
        ...

    async def list_invitations(self, org_id: str) -> List[InvitationInfo]:
        ...

    async def invite_member(
        self,
        org_id: str,
        inviter_id: str,
        inviter_name: str,
        invite_email: str,
        role_name: str,
    ) -> None:
        """Invite an email address to an organization.

        The inviter arrives as both an id and a name because the two backends
        need different things: Auth0 puts the name in the email it sends, while
        the local backend records a foreign key. Resolving one from the other is
        not safe -- display names are not unique.
        """
        ...

    async def get_invitation(
        self, org_id: str, invitation_id: str
    ) -> Optional[InvitationInfo]:
        """Return the invitation if it exists and is still redeemable."""
        ...

    async def accept_invitation(
        self, org_id: str, invitation: InvitationInfo, user_id: str
    ) -> None:
        """Retire the invitation, and register membership with the provider.

        Granting the roles is deliberately not part of this: authorization has
        always lived in our own database, so the caller does it once for every
        backend. See `grant_invitation_roles` in `api/views/auth.py`.
        """
        ...

    async def update_user(self, user_id: str, **kwargs) -> None:
        ...
