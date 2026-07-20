from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from aspen.auth.local_provisioning import LocalProvisioning
from aspen.database.models import (
    hash_invitation_token,
    Invitation,
    InvitationStatusType,
)
from aspen.test_infra.models.usergroup import group_factory, userrole_factory
from aspen.util.email import EmailSender

pytestmark = pytest.mark.asyncio


class RecordingEmailSender(EmailSender):
    def __init__(self):
        self.sent = []

    def send(self, to_address: str, subject: str, body: str) -> None:
        self.sent.append((to_address, subject, body))


async def setup_group(async_session: AsyncSession):
    group = group_factory()
    inviter = await userrole_factory(
        async_session,
        group,
        name="Inviter",
        email="inviter@dph.org",
        auth0_user_id="inviter",
        roles=["admin"],
    )
    async_session.add_all([group, inviter])
    await async_session.commit()
    return group, inviter


def token_from_email(body: str) -> str:
    _, _, rest = body.partition("invitation=")
    return rest.split("&")[0]


async def test_invite_member_stores_only_the_token_hash(async_session: AsyncSession):
    group, inviter = await setup_group(async_session)
    email_sender = RecordingEmailSender()
    provisioning = LocalProvisioning(
        async_session, email_sender, "http://backend.genepinet.localdev"
    )

    await provisioning.invite_member(
        group.auth0_org_id,
        inviter.auth0_user_id,
        inviter.name,
        "invitee@dph.org",
        "member",
    )

    invitation = (
        (await async_session.execute(sa.select(Invitation))).scalars().one()  # type: ignore
    )
    assert invitation.invitee_email == "invitee@dph.org"
    assert invitation.status == InvitationStatusType.PENDING

    (to_address, _, body) = email_sender.sent[0]
    assert to_address == "invitee@dph.org"
    token = token_from_email(body)
    # The raw token must never be recoverable from the database.
    assert token not in invitation.token_hash
    assert invitation.token_hash == hash_invitation_token(token)


async def test_get_invitation_round_trips_the_emailed_token(
    async_session: AsyncSession,
):
    group, inviter = await setup_group(async_session)
    email_sender = RecordingEmailSender()
    provisioning = LocalProvisioning(
        async_session, email_sender, "http://backend.genepinet.localdev"
    )
    await provisioning.invite_member(
        group.auth0_org_id,
        inviter.auth0_user_id,
        inviter.name,
        "invitee@dph.org",
        "member",
    )
    token = token_from_email(email_sender.sent[0][2])

    found = await provisioning.get_invitation(group.auth0_org_id, token)

    assert found is not None
    assert found["invitee_email"] == "invitee@dph.org"
    assert found["inviter_name"] == inviter.name
    assert found["roles"] == ["member"]
    assert await provisioning.get_invitation(group.auth0_org_id, "not-a-token") is None


async def test_invitation_cannot_be_redeemed_against_another_group(
    async_session: AsyncSession,
):
    """A token is only valid for the group it was issued for.

    `/process_invitation` takes the organization from the query string and
    grants roles in whatever group it names, so an unscoped token lookup would
    let a legitimate invitee escalate into a group they were never invited to.
    """
    group, inviter = await setup_group(async_session)
    other_group = group_factory(name="othergroup", auth0_org_id="local_other_group")
    async_session.add(other_group)
    await async_session.commit()

    email_sender = RecordingEmailSender()
    provisioning = LocalProvisioning(
        async_session, email_sender, "http://backend.genepinet.localdev"
    )
    await provisioning.invite_member(
        group.auth0_org_id,
        inviter.auth0_user_id,
        inviter.name,
        "invitee@dph.org",
        "member",
    )
    token = token_from_email(email_sender.sent[0][2])

    assert await provisioning.get_invitation(other_group.auth0_org_id, token) is None
    assert await provisioning.get_invitation(group.auth0_org_id, token) is not None


async def test_expired_invitations_are_neither_listed_nor_redeemable(
    async_session: AsyncSession,
):
    group, inviter = await setup_group(async_session)
    email_sender = RecordingEmailSender()
    provisioning = LocalProvisioning(
        async_session, email_sender, "http://backend.genepinet.localdev"
    )
    await provisioning.invite_member(
        group.auth0_org_id,
        inviter.auth0_user_id,
        inviter.name,
        "invitee@dph.org",
        "member",
    )
    token = token_from_email(email_sender.sent[0][2])

    invitation = (
        (await async_session.execute(sa.select(Invitation))).scalars().one()  # type: ignore
    )
    invitation.expires_at = datetime.now() - timedelta(days=1)
    await async_session.commit()

    assert await provisioning.list_invitations(group.auth0_org_id) == []
    assert await provisioning.get_invitation(group.auth0_org_id, token) is None


async def test_accept_invitation_retires_it(async_session: AsyncSession):
    group, inviter = await setup_group(async_session)
    email_sender = RecordingEmailSender()
    provisioning = LocalProvisioning(
        async_session, email_sender, "http://backend.genepinet.localdev"
    )
    await provisioning.invite_member(
        group.auth0_org_id,
        inviter.auth0_user_id,
        inviter.name,
        "invitee@dph.org",
        "member",
    )
    token = token_from_email(email_sender.sent[0][2])
    info = await provisioning.get_invitation(group.auth0_org_id, token)
    assert info is not None

    await provisioning.accept_invitation(group.auth0_org_id, info, "invitee")

    invitation = (
        (await async_session.execute(sa.select(Invitation))).scalars().one()  # type: ignore
    )
    assert invitation.status == InvitationStatusType.ACCEPTED
    assert invitation.accepted_at is not None
    # A retired invitation cannot be redeemed a second time.
    assert await provisioning.get_invitation(group.auth0_org_id, token) is None
    assert await provisioning.list_invitations(group.auth0_org_id) == []
