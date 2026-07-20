import re

import sentry_sdk
import sqlalchemy as sa
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, joinedload
from sqlalchemy.sql.expression import Select

from aspen.api.authn import get_admin_user, get_auth_user, get_identity_provider
from aspen.api.authz import fetch_authorized_row, require_access
from aspen.api.deps import get_db
from aspen.api.error import http_exceptions as ex
from aspen.api.schemas.usergroup import (
    GroupCreationRequest,
    GroupInfoResponse,
    GroupInvitationsRequest,
    GroupInvitationsResponse,
    GroupMembersResponse,
    InvitationsResponse,
)
from aspen.auth.identity_provider import IdentityProvider, InvitationInfo
from aspen.database.models import Group, User, UserRole

router = APIRouter()


def _serialize_invitation(invitation: InvitationInfo) -> dict:
    return {
        "id": invitation["id"],
        "created_at": invitation["created_at"],
        "expires_at": invitation["expires_at"],
        "inviter": {"name": invitation["inviter_name"]},
        "invitee": {"email": invitation["invitee_email"]},
    }


@router.post("/", response_model=GroupInfoResponse)
async def create_group(
    group_creation_request: GroupCreationRequest,
    db: AsyncSession = Depends(get_db),
    identity_provider: IdentityProvider = Depends(get_identity_provider),
    user: User = Depends(get_admin_user),
) -> GroupInfoResponse:
    # Auth0 requires we only have alphanumerics, "-" and "_" in a group name.
    # This regex replaces all other characters with an underscore, "_".
    # There is also a 50 character limit, but we limit prefixes to 20 characters
    # anyways.
    auth0_safe_prefix = re.sub(
        r"[^a-zA-Z0-9_-]+", "_", group_creation_request.prefix.lower()
    )
    org_id = await identity_provider.create_org(
        auth0_safe_prefix, group_creation_request.name
    )
    group_values = dict(group_creation_request) | {"auth0_org_id": org_id}
    group = Group(**group_values)
    db.add(group)
    await db.commit()

    created_group_query = (
        sa.select(Group)  # type: ignore
        .options(joinedload(Group.default_tree_location))
        .where(Group.auth0_org_id == group.auth0_org_id)
    )
    created_group_query_result = await db.execute(created_group_query)
    created_group = created_group_query_result.scalars().one()
    return GroupInfoResponse.from_orm(created_group)


@router.get("/{group_id}/", response_model=GroupInfoResponse)
async def get_group_info(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    authorized_query: Select = Depends(require_access("read", Group)),
) -> GroupInfoResponse:
    group_query = authorized_query.options(  # type: ignore
        joinedload(Group.default_tree_location)
    ).where(Group.id == group_id)
    group_query_result = await db.execute(group_query)
    group = group_query_result.scalars().one_or_none()
    if not group:
        raise ex.BadRequestException("Not found")
    return GroupInfoResponse.from_orm(group)


@router.get("/{group_id}/members/", response_model=GroupMembersResponse)
async def get_group_members(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    group: Group = Depends(fetch_authorized_row("read", Group, "group_id")),
) -> GroupMembersResponse:
    members_query = (
        sa.select(User)  # type: ignore
        .join(User.user_roles)  # type: ignore
        .join(UserRole.role)  # type: ignore
        .options(  # type: ignore
            contains_eager(User.user_roles).contains_eager(UserRole.role)
        )
        .where(UserRole.group == group)
    )

    members = (await db.execute(members_query)).unique().scalars().all()
    group_members = []
    # users can only have one role per group
    for member in members:
        member.role = next(
            user_role.role.name
            for user_role in member.user_roles
            if user_role.group.id == group_id
        )
        group_members.append(member)
    return GroupMembersResponse.parse_obj({"members": group_members})


@router.get("/{group_id}/invitations/", response_model=InvitationsResponse)
async def get_group_invitations(
    group: Group = Depends(fetch_authorized_row("read", Group, "group_id")),
    identity_provider: IdentityProvider = Depends(get_identity_provider),
) -> InvitationsResponse:
    try:
        invitations = await identity_provider.list_invitations(group.auth0_org_id)
    except Exception:
        raise ex.BadRequestException("Not found")
    return InvitationsResponse.parse_obj(
        {"invitations": [_serialize_invitation(i) for i in invitations]}
    )


@router.post("/{group_id}/invitations/", response_model=GroupInvitationsResponse)
async def invite_group_members(
    group_invitation_request: GroupInvitationsRequest,
    identity_provider: IdentityProvider = Depends(get_identity_provider),
    group: Group = Depends(fetch_authorized_row("invite_members", Group, "group_id")),
    user: User = Depends(get_auth_user),
) -> GroupInvitationsResponse:
    responses = []
    for email in group_invitation_request.emails:
        success = True
        try:
            await identity_provider.invite_member(
                group.auth0_org_id,
                user.auth0_user_id,
                user.name,
                email,
                group_invitation_request.role,
            )
        except Exception as err:
            # TODO - we need to learn more about possible exceptions here.
            sentry_sdk.capture_exception(err)
            success = False
        responses.append({"email": email, "success": success})
    return GroupInvitationsResponse.parse_obj({"invitations": responses})
