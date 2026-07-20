"""Database-backed implementation of the provisioning interface.

Organizations are just `groups` rows, membership and roles are `user_roles`
rows, and invitations live in `invitations`. Nothing here talks to an external
identity provider.
"""

import uuid
from datetime import datetime, timedelta
from typing import List, Optional
from urllib.parse import urlencode

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from sqlalchemy.orm.exc import NoResultFound

from aspen.auth.identity_provider import InvitationInfo
from aspen.database.models import (
    generate_invitation_token,
    Group,
    hash_invitation_token,
    Invitation,
    InvitationStatusType,
    Role,
    User,
)
from aspen.util.email import EmailSender

INVITATION_SUBJECT = "You have been invited to join a group on CZ Gen Epi"


def _to_invitation_info(invitation: Invitation) -> InvitationInfo:
    return {
        "id": str(invitation.id),
        "created_at": invitation.created_at.isoformat(),
        "expires_at": invitation.expires_at.isoformat(),
        "inviter_name": invitation.invited_by.name,
        "invitee_email": invitation.invitee_email,
        "roles": [invitation.role.name],
    }


class LocalProvisioning:
    def __init__(
        self,
        db: AsyncSession,
        email_sender: EmailSender,
        api_url: str,
        expiry_days: int = 14,
    ) -> None:
        self.db = db
        self.email_sender = email_sender
        self.api_url = api_url
        self.expiry_days = expiry_days

    async def _get_group(self, org_id: str) -> Group:
        return (
            (await self.db.execute(sa.select(Group).where(Group.auth0_org_id == org_id)))  # type: ignore
            .scalars()
            .one()
        )

    async def create_org(self, group_prefix: str, group_name: str) -> str:
        # Groups.auth0_org_id only has to be unique and stable; the prefix keeps
        # it readable when debugging.
        return f"local_{group_prefix}_{uuid.uuid4().hex[:12]}"

    async def list_invitations(self, org_id: str) -> List[InvitationInfo]:
        group = await self._get_group(org_id)
        invitations = (
            (
                await self.db.execute(
                    sa.select(Invitation)  # type: ignore
                    .options(
                        joinedload(Invitation.invited_by), joinedload(Invitation.role)
                    )
                    .where(Invitation.group_id == group.id)
                    .where(Invitation.status == InvitationStatusType.PENDING)
                    .where(Invitation.expires_at > datetime.now())
                )
            )
            .scalars()
            .all()
        )
        return [_to_invitation_info(invitation) for invitation in invitations]

    async def invite_member(
        self,
        org_id: str,
        inviter_id: str,
        inviter_name: str,
        invite_email: str,
        role_name: str,
    ) -> None:
        group = await self._get_group(org_id)
        role = (
            (await self.db.execute(sa.select(Role).where(Role.name == role_name)))  # type: ignore
            .scalars()
            .one()
        )
        # Looked up by id rather than by `inviter_name`: names are display
        # values and are not unique.
        inviter = (
            (
                await self.db.execute(
                    sa.select(User).where(User.auth0_user_id == inviter_id)  # type: ignore
                )
            )
            .scalars()
            .one()
        )

        token = generate_invitation_token()
        invitation = Invitation(
            group_id=group.id,
            role_id=role.id,
            invited_by_user_id=inviter.id,
            invitee_email=invite_email,
            token_hash=hash_invitation_token(token),
            status=InvitationStatusType.PENDING,
            expires_at=datetime.now() + timedelta(days=self.expiry_days),
        )
        self.db.add(invitation)
        await self.db.commit()

        self.email_sender.send(
            invite_email,
            INVITATION_SUBJECT,
            self._invitation_body(group, inviter, token, org_id),
        )

    def _invitation_body(
        self, group: Group, inviter: User, token: str, org_id: str
    ) -> str:
        params = urlencode({"invitation": token, "organization": org_id})
        accept_url = f"{self.api_url}/v2/auth/process_invitation?{params}"
        return (
            f"{inviter.name} has invited you to join {group.name} on CZ Gen Epi.\n\n"
            f"Accept the invitation here: {accept_url}\n\n"
            f"This invitation expires in {self.expiry_days} days."
        )

    async def _get_redeemable(self, org_id: str, token: str) -> Optional[Invitation]:
        """Look up an invitation by token, scoped to the organization.

        The scoping is load-bearing, not defensive tidiness. Callers take
        `org_id` straight from a query string, and the roles are later granted
        in whatever group that id names. Without the join below, a valid token
        for one group could be replayed against another to gain roles there.
        """
        try:
            group = await self._get_group(org_id)
        except NoResultFound:
            return None
        try:
            invitation = (
                (
                    await self.db.execute(
                        sa.select(Invitation)  # type: ignore
                        .options(
                            joinedload(Invitation.invited_by),
                            joinedload(Invitation.role),
                        )
                        .where(Invitation.token_hash == hash_invitation_token(token))
                        .where(Invitation.group_id == group.id)
                    )
                )
                .scalars()
                .one()
            )
        except NoResultFound:
            return None
        if not invitation.is_redeemable():
            return None
        return invitation

    async def get_invitation(
        self, org_id: str, invitation_id: str
    ) -> Optional[InvitationInfo]:
        # For this backend the "invitation id" in the accept URL is the raw
        # token; only its hash is stored. See `_invitation_body`.
        invitation = await self._get_redeemable(org_id, invitation_id)
        if invitation is None:
            return None
        return _to_invitation_info(invitation)

    async def accept_invitation(
        self, org_id: str, invitation: InvitationInfo, user_id: str
    ) -> None:
        stored = (
            (
                await self.db.execute(
                    sa.select(Invitation).where(Invitation.id == int(invitation["id"]))  # type: ignore
                )
            )
            .scalars()
            .one()
        )
        if not stored.is_redeemable():
            return
        # Granting the roles is the caller's job -- see `grant_invitation_roles`.
        # It is identical for every backend, because authorization has always
        # lived in our own database.
        stored.status = InvitationStatusType.ACCEPTED
        stored.accepted_at = datetime.now()
        await self.db.commit()

    async def update_user(self, user_id: str, **kwargs) -> None:
        # User profile fields already live in our database; the views update them
        # directly, so there is nothing to propagate anywhere else.
        return None
