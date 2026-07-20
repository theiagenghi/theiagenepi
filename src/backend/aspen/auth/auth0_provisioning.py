"""Auth0 implementation of the provisioning interface.

Wraps the existing synchronous `Auth0Client`. Every call goes through a
threadpool because `Auth0Client` uses blocking HTTP, and the callers are async
request handlers.
"""

from datetime import datetime
from typing import List, Optional

from starlette.concurrency import run_in_threadpool

from aspen.auth.auth0_management import Auth0Client, Auth0Org
from aspen.auth.identity_provider import InvitationInfo


def _as_org(org_id: str) -> Auth0Org:
    # Auth0Org is a TypedDict the client only reads "id" from, but every field
    # is required, so the other two are filled in with blanks.
    return {"id": org_id, "name": "", "display_name": ""}


def _to_invitation_info(ticket) -> InvitationInfo:
    return {
        "id": ticket["id"],
        "created_at": ticket["created_at"],
        "expires_at": ticket["expires_at"],
        "inviter_name": ticket["inviter"]["name"],
        "invitee_email": ticket["invitee"]["email"],
        "roles": ticket.get("roles", []),
    }


class Auth0Provisioning:
    def __init__(self, client: Auth0Client, client_id: str) -> None:
        self.client = client
        # Auth0 requires the application client_id on invitations so the
        # invitation email links back to the right application.
        self.client_id = client_id

    async def create_org(self, group_prefix: str, group_name: str) -> str:
        org = await run_in_threadpool(self.client.add_org, group_prefix, group_name)
        return org["id"]

    async def list_invitations(self, org_id: str) -> List[InvitationInfo]:
        tickets = await run_in_threadpool(
            self.client.get_org_invitations, _as_org(org_id)
        )
        return [_to_invitation_info(ticket) for ticket in tickets]

    async def invite_member(
        self,
        org_id: str,
        inviter_id: str,
        inviter_name: str,
        invite_email: str,
        role_name: str,
    ) -> None:
        # Auth0 identifies the inviter by the name it renders in the email it
        # sends, so `inviter_id` has nothing to map onto here.
        await run_in_threadpool(
            self.client.invite_member,
            org_id,
            self.client_id,
            inviter_name,
            invite_email,
            role_name,
        )

    async def get_invitation(
        self, org_id: str, invitation_id: str
    ) -> Optional[InvitationInfo]:
        # Auth0 has no lookup by ticket id, so we scan the organization's
        # outstanding invitations.
        tickets = await run_in_threadpool(
            self.client.get_org_invitations, _as_org(org_id)
        )
        for ticket in tickets:
            # Auth0 stamps expiry in UTC, so this must compare against UTC.
            # `datetime.now()` would be wrong by the host's offset -- silently
            # correct on a UTC container, silently wrong anywhere else.
            expires = ticket["expires_at"]
            if datetime.fromisoformat(expires.rstrip("Z")) < datetime.utcnow():
                continue
            if ticket.get("ticket_id") == invitation_id:
                info = _to_invitation_info(ticket)
                info["id"] = ticket["id"]
                return info
        return None

    async def accept_invitation(
        self, org_id: str, invitation: InvitationInfo, user_id: str
    ) -> None:
        await run_in_threadpool(
            self.client.add_org_member,
            _as_org(org_id),
            user_id,
            invitation["roles"],
            False,
        )
        await run_in_threadpool(
            self.client.delete_organization_invitation, org_id, invitation["id"]
        )

    async def update_user(self, user_id: str, **kwargs) -> None:
        await run_in_threadpool(self.client.update_user, user_id, **kwargs)
